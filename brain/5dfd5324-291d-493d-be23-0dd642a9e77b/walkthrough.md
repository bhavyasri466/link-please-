# Walkthrough — LinkPlease Automation Engine

We have successfully engineered and verified the complete **LinkPlease Automation Engine** solving Parts A, B, and C of the Tech Intern Assignment.

---

## 📦 What Was Built

```
linkplease-automation/
├── app/
│   ├── __init__.py
│   ├── config.py              # Configuration & rate limit constants
│   ├── database.py            # SQLite WAL persistent queue & atomic deduplication
│   ├── main.py                # FastAPI app with exact required routes & lifespan
│   ├── rate_limiter.py        # Token-bucket & sliding window rate limiter (≤10/min)
│   ├── security.py            # HMAC-SHA256 signature verification
│   ├── worker.py              # Outbound dispatch worker & DM reconciliation worker
│   └── static/
│       ├── index.html         # Realtime monitoring dashboard
│       ├── style.css          # Modern dark-mode UI
│       └── app.js             # Live polling & interactive controls
├── tests/
│   ├── __init__.py
│   ├── test_server.py         # Pytest suite covering all 3 parts
│   └── run_mock_simulation.py # Local hostile mock API server & simulation harness
├── tools/
│   └── api_helper.py          # CLI for apply, keygen, simulation, truth, submit
├── FAILURES.md                # Candid breakdown of failure modes & edge cases
├── LOOM_SCRIPT.md             # 3-minute video presentation script
├── README.md                  # Comprehensive setup, deployment & usage guide
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container image configuration
└── .env.example / .env        # Environment variables template
```

---

## 🎯 Verification & Automated Test Results

### 1. Pytest Suite Execution
All unit and integration tests passed cleanly:
```
tests/test_server.py::test_create_rule PASSED                            [ 14%]
tests/test_server.py::test_webhook_rule_matching_and_deduplication PASSED [ 28%]
tests/test_server.py::test_event_id_redelivery_deduplication PASSED      [ 42%]
tests/test_server.py::test_signature_verification PASSED                 [ 57%]
tests/test_server.py::test_stats_reporting_structure PASSED              [ 71%]
tests/test_server.py::test_comment_deleted_before_send_cancels_dm PASSED [ 85%]
tests/test_server.py::test_dm_job_state_transitions PASSED               [100%]

======================== 7 passed in 3.64s =========================
```

---

## 🔍 Core Features Implemented

### Part A (Required)
- **`POST /rules`**: Case-insensitive keyword rule registration with `201 Created`.
- **`POST /webhook`**: Sub-50ms ingestion responding with `200 OK`.
- **Atomic Deduplication**: Compound primary key `(user_id, rule_id)` guarantees the same user is never DMed twice for the same rule.
- **Crash-Safe Queue**: Persistent SQLite in WAL mode ensures in-flight DMs and retries survive host process restarts.

### Part B
- **HMAC-SHA256 Verification**: Webhook requests are verified using `X-PseudoGram-Signature: sha256=<hex>` against the API secret.
- **`GET /stats`**: Live, honest metric reporting for `sent`, `failed`, `queued`, and `duplicates_blocked`.

### Part C
- **DM Delivery Status Reconciliation**: Background worker polls `GET /v1/dm/{dm_id}` to detect when an accepted DM fails on the mock platform, and automatically triggers an exponential backoff re-send.
- **`comment.deleted` Handling**: Cancels queued DMs before dispatch if the user deleted their comment.
- **Burst Rate Limiting**: Outbound dispatcher enforces a strict ≤ 10 requests per rolling 60s rate limit to prevent upstream 429s during 500-comment bursts.

---

## 🚀 How to Run and Test

### 1. Start the Server Locally
```powershell
cd C:\Users\bhavy\.gemini\antigravity\scratch\linkplease-automation
uvicorn app.main:app --port 8000 --reload
```
Open **`http://localhost:8000`** to view the live dashboard.

### 2. Apply and Submit to Mock API
Use the CLI helper in `tools/api_helper.py`:
```powershell
# 1. Apply
python tools/api_helper.py --apply --name "Your Name" --email "you@example.com" --phone "+91..." --linkedin "https://linkedin.com/in/you"

# 2. Get API Key
python tools/api_helper.py --keygen --email "you@example.com"

# 3. Fire Simulation
python tools/api_helper.py --simulate --api-key "<KEY>" --webhook-url "https://<your-deployed-url>/webhook" --count 500 --duration 10

# 4. Check Ground Truth
python tools/api_helper.py --truth "<RUN_ID>" --api-key "<KEY>"

# 5. Submit Assignment
python tools/api_helper.py --submit --email "you@example.com" --github-repo "https://github.com/you/repo" --working-url "https://<deployed-url>" --loom-url "https://loom.com/share/..."
```
