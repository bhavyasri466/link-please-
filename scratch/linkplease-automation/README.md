# LinkPlease Automation Engine

> **High-Reliability Instagram Comment-to-DM Engine built for Hostile Platform APIs.**
> Developed for the **LinkPlease Tech Intern Assignment**.

---

## 🚀 Overview

LinkPlease automates Instagram interactions for creators: when someone comments a keyword (e.g. `PRICE`, `CATALOG`, `LINK`) on a creator's post, the engine delivers the corresponding DM to that user.

This implementation is built on top of a hostile mock platform API (`https://pseudogram-api.onrender.com`) and satisfies all requirements across **Part A**, **Part B**, and **Part C**:

| Tier | Feature | Implementation Status |
|---|---|:---:|
| **Part A** | Case-insensitive rule creation (`POST /rules`) & sub-50ms ingestion (`POST /webhook`) | ✅ Completed |
| **Part A** | Zero duplicate DMs per user per rule (`(user_id, rule_id)` atomic constraint) | ✅ Completed |
| **Part A** | Crash-safe persistent SQLite queue (WAL mode) with exponential backoff retries on failures | ✅ Completed |
| **Part B** | HMAC-SHA256 signature verification (`X-PseudoGram-Signature: sha256=<hex>`) | ✅ Completed |
| **Part B** | Live, accurate stats tracking on `GET /stats` (Sent, Failed, Queued, Duplicates Blocked) | ✅ Completed |
| **Part C** | Asynchronous DM status reconciliation (polling `GET /v1/dm/{dm_id}` & retrying accepted DMs that fail) | ✅ Completed |
| **Part C** | Smart handling of `comment.deleted` events (cancels pending DM before sending) | ✅ Completed |
| **Part C** | Token-bucket & sliding window rate limiter strictly enforcing ≤10 req/60s under 500-comment bursts | ✅ Completed |

---

## 📐 Architecture & Data Flow

```
[ Incoming Webhook ] ──> HMAC-SHA256 Signature Verification
                              │
                    ┌─────────┴─────────┐
             (Invalid 401)       (Valid 200)
                                      │
                              Event Deduplication (event_id)
                                      │
                     ┌────────────────┴────────────────┐
             (comment.deleted)                (comment.created)
                     │                                 │
          Cancel Pending DM Job               Case-Insensitive Rule Match
                                                       │
                                          Atomic Unique (user_id, rule_id)
                                                       │
                                          ┌────────────┴────────────┐
                                   (Duplicate)               (New Match)
                                        │                         │
                               Count duplicates_blocked     Enqueue DM Job (SQLite WAL)
                                                                  │
                                                     [ Outbound Rate Limiter ]
                                                     (Token Bucket: ≤10 req/min)
                                                                  │
                                                        POST /v1/dm/send
                                                                  │
                                                     ┌────────────┴────────────┐
                                              (202 Accepted)            (500 / 429 Error)
                                                     │                         │
                                            Status: 'reconciling'      Exp Backoff Retry
                                                     │
                                            [ Reconciliation Worker ]
                                            Polls GET /v1/dm/{dm_id}
                                                     │
                                         ┌───────────┴───────────┐
                                   (delivered)                (failed)
                                        │                         │
                                  Stats: sent++             Retry or fail++
```

---

## 🛠️ Non-Negotiable API Contracts

### 1. `POST /rules`
```bash
curl -X POST http://localhost:8000/rules \
  -H "Content-Type: application/json" \
  -d '{ "keyword": "PRICE", "dm_message": "Here is the price list: https://example.com/prices" }'
```
**Response (201 Created):**
```json
{
  "rule_id": "rule_a1b2c3d4e5f6",
  "keyword": "PRICE",
  "dm_message": "Here is the price list: https://example.com/prices"
}
```

### 2. `POST /webhook`
```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-PseudoGram-Signature: sha256=..." \
  -d '{
    "event_id": "evt_01J8ZQ4K2N7RXA",
    "event_type": "comment.created",
    "sent_at": "2026-08-10T09:14:22.481Z",
    "data": {
      "comment_id": "cmt_9f2a7c",
      "post_id": "post_44de1b",
      "text": "PRICE please 🙏",
      "created_at": "2026-08-10T09:14:21.900Z",
      "from": {
        "user_id": "usr_3b91fe",
        "username": "arjun.shoots"
      }
    }
  }'
```
**Response (200 OK):**
```json
{ "status": "ok" }
```

### 3. `GET /stats`
```bash
curl http://localhost:8000/stats
```
**Response (200 OK):**
```json
{
  "sent": 142,
  "failed": 3,
  "queued": 8,
  "duplicates_blocked": 57
}
```

---

## 💻 Quick Start & Running Locally

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
*(Optional: add your `PSEUDOGRAM_API_KEY` obtained from keygen)*

### 3. Run Application Server
```bash
uvicorn app.main:app --reload --port 8000
```
Visit the interactive dashboard at **`http://localhost:8000`** or OpenAPI documentation at **`http://localhost:8000/docs`**.

---

## 🧪 Running Automated Tests

Run the full pytest suite:
```bash
pytest -v tests/
```

Run the local Mock API stress simulation harness:
```bash
python tests/run_mock_simulation.py
```

---

## 🔧 Developer Toolkit & Simulation Runner (`tools/api_helper.py`)

A unified CLI utility is included for interaction with the mock API:

### 1. Apply for API Access
```bash
python tools/api_helper.py --apply \
  --name "Your Name" \
  --email "you@example.com" \
  --phone "+919876543210" \
  --linkedin "https://linkedin.com/in/yourprofile"
```

### 2. Obtain Your API Key
```bash
python tools/api_helper.py --keygen --email "you@example.com"
```

### 3. Run 500-Event Stress Simulation
```bash
python tools/api_helper.py --simulate \
  --api-key "YOUR_KEY" \
  --webhook-url "https://your-deployed-app.onrender.com/webhook" \
  --count 500 \
  --duration 10
```

### 4. Verify Ground Truth
```bash
python tools/api_helper.py --truth "<RUN_ID>" --api-key "YOUR_KEY"
```

### 5. Submit Final Assignment
```bash
python tools/api_helper.py --submit \
  --email "you@example.com" \
  --github-repo "https://github.com/your-username/linkplease-automation" \
  --working-url "https://your-deployed-app.onrender.com" \
  --loom-url "https://loom.com/share/..." \
  --parts "A+B+C" \
  --start-date "2026-08-25"
```

---

## 🚢 Deployment Guide (Render / Railway)

### Deploying to Render
1. Push this repository to a public GitHub repo.
2. Log into [Render Dashboard](https://dashboard.render.com).
3. Click **New +** -> **Web Service**.
4. Select your repository.
5. Set:
   - **Environment:** `Python`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Add Environment Variable:
   - `PSEUDOGRAM_API_KEY` = your generated API key.
7. Click **Create Web Service**. Your service URL will be `https://<service-name>.onrender.com`.

---

## 📄 Key Documents

- [`FAILURES.md`](./FAILURES.md): Honest analysis of failure modes, distributed concurrency edge cases, SQLite lock contention, and rate limit queuing latency.
- [`LOOM_SCRIPT.md`](./LOOM_SCRIPT.md): 3-minute video presentation outline and talking points.
