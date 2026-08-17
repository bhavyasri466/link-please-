# LinkPlease Tech Intern Assignment — Complete Architecture & Implementation Plan

## Goal
Build a high-reliability, production-ready backend service that automates Instagram DMs for creators via the hostile mock API (`https://pseudogram-api.onrender.com`).
The system strictly fulfills:
- **Part A**: Case-insensitive rule matching (`POST /rules`), fast webhook ingestion (`POST /webhook`), strict per-rule-per-user deduplication, persistent retries on API failures.
- **Part B**: Webhook signature verification (`X-PseudoGram-Signature: sha256=...` HMAC-SHA256), live and accurate numbers on `GET /stats`.
- **Part C**: Asynchronous DM status reconciliation (`GET /v1/dm/{dm_id}` polling and retrying accepted DMs that failed later), `comment.deleted` event handling, and 500-event burst queue management strictly honoring the 10 req/60s rate limit without losing any events.
- **Documentation & Artifacts**: Honest `FAILURES.md`, automated test suite with mock server simulations, interactive developer dashboard, and complete submission / Loom script guide.

---

## User Review Required
> [!IMPORTANT]
> The mock API enforces a strict rate limit of **10 requests per rolling 60 seconds** on `POST /v1/dm/send`. When handling a burst of 500 comments, our system uses a persistent queue with a leaky-bucket/token-bucket dispatcher pacing sends at ~6.1 seconds per request, with dynamic pause on any `429 Retry-After`. Reads (`GET /v1/dm/{dm_id}`) do not count against the rate limit and are polled separately.

> [!NOTE]
> Database choice: We use **SQLite with Write-Ahead Logging (WAL)** and atomic transactions (`user_rule_executions` unique constraint). This provides complete persistence across server restarts (avoiding in-memory data loss) without requiring external database server setup, while remaining easily portable to PostgreSQL if desired.

---

## Proposed System Architecture

```mermaid
flowchart TD
    subgraph Ingestion ["Ingestion Layer (POST /webhook)"]
        W[Incoming Webhook] --> SIG[HMAC-SHA256 Verification]
        SIG -->|Invalid| R401[401 / 403 Reject]
        SIG -->|Valid| DEDUP_EVT{event_id in DB?}
        DEDUP_EVT -->|Yes Duplicate| DEDUP_INC[Increment duplicates_blocked & Return 200]
        DEDUP_EVT -->|No| SAVE_EVT[Store event_id]
        SAVE_EVT --> TYPE{Event Type}
        TYPE -->|comment.deleted| DEL[Mark comment deleted & Cancel pending DM]
        TYPE -->|comment.created| MATCH[Match comment text against Rules]
        MATCH --> DEDUP_USER{User already DMed for Rule?}
        DEDUP_USER -->|Yes| DEDUP_INC
        DEDUP_USER -->|No| ENQUEUE[Insert user_rule_execution & Enqueue dm_job]
        ENQUEUE --> R200[Immediate 200 OK (<50ms)]
    end

    subgraph Worker ["Outbound Rate Limiter & Queue Worker"]
        ENQUEUE -.-> Q[(Persistent SQLite dm_jobs)]
        Q --> DISPATCH[Token Bucket Rate Limiter: <=10 req/60s]
        DISPATCH --> CHECK_DEL{Was comment deleted?}
        CHECK_DEL -->|Yes| CANCEL[Mark Cancelled / duplicates_blocked]
        CHECK_DEL -->|No| SEND[POST /v1/dm/send with Idempotency-Key]
        SEND -->|202 Accepted| REC_QUEUE[Set status = reconciling]
        SEND -->|429 Rate Limited| PAUSE[Pause until Retry-After]
        SEND -->|500 / Network Error| RETRY[Exp Backoff retry_count++]
        SEND -->|400 Malformed| FAIL[Mark Failed]
    end

    subgraph Reconciliation ["Reconciliation Engine (Part C)"]
        REC_QUEUE --> POLL[Poll GET /v1/dm/dm_id]
        POLL -->|status = delivered| DELIV[Set status = delivered -> Stats: sent++]
        POLL -->|status = failed| RE_SEND{Retries remaining?}
        RE_SEND -->|Yes| Q
        RE_SEND -->|No| FAIL[Set status = failed -> Stats: failed++]
        POLL -->|status = queued| WAIT[Poll again after interval]
    end

    subgraph API_Endpoints ["Public Endpoints"]
        R_POST[POST /rules]
        W_POST[POST /webhook]
        S_GET[GET /stats]
        UI_GET[GET / Dashboard]
    end
```

---

## Proposed Changes

We will construct a modular, production-ready Python project in `linkplease-automation`:

### 1. Core Service & Database Layer

#### [NEW] [config.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/config.py)
- Configuration settings (API Key, Base URL, Database Path, Rate Limits, Max Retries, Polling Intervals) using `pydantic-settings` or `os.environ`.

#### [NEW] [database.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/database.py)
- SQLite database initialization with WAL mode (`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`).
- Schema creation:
  - `rules`: `rule_id` (TEXT PK), `keyword` (TEXT), `dm_message` (TEXT), `created_at` (TEXT)
  - `events`: `event_id` (TEXT PK), `event_type` (TEXT), `received_at` (TEXT)
  - `comments`: `comment_id` (TEXT PK), `post_id` (TEXT), `user_id` (TEXT), `text` (TEXT), `is_deleted` (INTEGER), `created_at` (TEXT)
  - `user_rule_executions`: `user_id` (TEXT), `rule_id` (TEXT), `comment_id` (TEXT), `created_at` (TEXT), PRIMARY KEY (`user_id`, `rule_id`) -> **guarantees zero duplicate DMs per user per rule**
  - `dm_jobs`: `job_id` (TEXT PK), `user_id` (TEXT), `rule_id` (TEXT), `comment_id` (TEXT), `message` (TEXT), `status` (TEXT: `queued`, `sending`, `reconciling`, `delivered`, `failed`, `cancelled`), `idempotency_key` (TEXT), `dm_id` (TEXT), `retry_count` (INTEGER), `next_retry_at` (REAL), `last_error` (TEXT), `created_at` (REAL), `updated_at` (REAL)
  - `stats_log`: `event_name` (TEXT), `occurred_at` (REAL) for fast tracking of `duplicates_blocked`.

#### [NEW] [security.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/security.py)
- HMAC-SHA256 signature verification matching `X-PseudoGram-Signature: sha256=<hex>` over the raw request body with constant-time comparison `hmac.compare_digest`.

#### [NEW] [rate_limiter.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/rate_limiter.py)
- Async Rate Limiter enforcing the 10 req / 60s rule (~6.05s between requests or 10-token sliding window).
- Dynamic handling for 429 `Retry-After` headers.

#### [NEW] [worker.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/worker.py)
- Async background tasks:
  1. **Send Worker**: Fetches `queued` jobs where `next_retry_at <= now`, verifies comment is not deleted, dispatches via `POST /v1/dm/send` with `Idempotency-Key`, updates status to `reconciling` or schedules retry.
  2. **Reconciliation Worker**: Periodically queries `GET /v1/dm/{dm_id}` for all `reconciling` jobs, marks `delivered` or resets to `queued` if failed (up to max retries).

#### [NEW] [main.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/main.py)
- FastAPI app with lifecycle hooks (`lifespan`) starting background workers.
- Exact non-negotiable routes:
  - `POST /webhook` (returns 200 within 5 seconds, background ingestion).
  - `POST /rules` (`{"keyword": "...", "dm_message": "..."}` -> 201 `{"rule_id": "...", "keyword": "...", "dm_message": "..."}`).
  - `GET /stats` (`{"sent": ..., "failed": ..., "queued": ..., "duplicates_blocked": ...}`).
- Developer endpoints:
  - `GET /health`: Health check.
  - `GET /rules`: List all registered rules.
  - `GET /jobs`: Inspect DM jobs and statuses.
  - `POST /simulate/trigger`: Helper to trigger `/v1/simulate/start` on the mock API directly from the UI.
  - Dashboard UI serving HTML + Vanilla CSS + real-time status.

---

### 2. Frontend / Developer Dashboard

#### [NEW] [static/index.html](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/static/index.html) & [static/app.js](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/app/static/app.js)
- Clean, responsive dashboard to:
  - View live `/stats` (auto-refreshing counter cards for Sent, Failed, Queued, Duplicates Blocked).
  - Create and manage automation rules with instant testing.
  - View live stream of incoming events and outgoing DMs.
  - Test / simulate 500-event stress tests and view ground truth comparison directly.

---

### 3. Failures Documentation & Submission Toolkit

#### [NEW] [FAILURES.md](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/FAILURES.md)
- Brutally honest, comprehensive analysis detailing real failure modes:
  1. *Host Process Crash during HTTP dispatch vs DB state transaction.*
  2. *SQLite write contention under extreme concurrency (500 events in 10s).*
  3. *Rate-limit backlog queue latency: 500 comments at 10 DMs/min takes ~50 minutes of sustained queue draining.*
  4. *Delayed `comment.deleted` arriving AFTER `POST /v1/dm/send` returns 202 Accepted.*
  5. *Mock API permanent 500s exceeding max retries.*

#### [NEW] [LOOM_SCRIPT.md](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/LOOM_SCRIPT.md)
- Concise 3-minute video presentation outline answering:
  1. *Tradeoff made (e.g. strict SQLite transactional serializability vs in-memory lockless speed) and what was sacrificed.*
  2. *What would be done differently with one more week (e.g. distributed Redis stream worker, multi-worker leader election, partitioned user queues).*

#### [NEW] [tools/api_helper.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/tools/api_helper.py)
- CLI script to automate:
  - `apply` and `keygen` on `https://pseudogram-api.onrender.com`
  - Start simulation (`/v1/simulate/start`)
  - Fetch ground truth (`/v1/simulate/{run_id}/truth`)
  - Compare local `/stats` against truth
  - Submit application (`POST /v1/submit`)

---

### 4. Automated Testing Suite

#### [NEW] [tests/test_server.py](file:///C:/Users/bhavy/.gemini/antigravity/scratch/linkplease-automation/tests/test_server.py)
- Complete unit and integration test suite:
  - Rule creation and case-insensitive matching.
  - Webhook signature validation (valid, missing, forged).
  - Deduplication: Duplicate `event_id` redelivery.
  - Deduplication: Same user commenting multiple times on same or different rules.
  - Handling `comment.deleted` before send.
  - Outbound rate-limiting test (simulating 429 Retry-After).
  - Outbound reconciliation test (simulating 15% delayed failures and retries).
  - 500-event concurrency stress test with local mock API server.

---

## Verification Plan

### Automated Tests
1. Run `pytest` with 100% coverage on rule matching, signature verification, deduplication, retry mechanics, and rate limits:
   ```powershell
   pytest -v tests/
   ```
2. Run local mock server + 500 event simulation test:
   ```powershell
   python tests/run_mock_simulation.py
   ```

### Manual Verification & Live API Testing
1. Apply and generate an API key via `python tools/api_helper.py --apply --keygen`.
2. Launch FastAPI service (`uvicorn app.main:app --port 8000`).
3. Connect local server to public URL (ngrok/localtunnel) or deploy to Render/Railway.
4. Run live simulation against `https://pseudogram-api.onrender.com/v1/simulate/start`.
5. Compare local `GET /stats` against `GET /v1/simulate/{run_id}/truth`.
