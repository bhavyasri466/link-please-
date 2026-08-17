# 3-Minute Loom Video Script & Talking Points

> **Objective:** Deliver a clear, authentic 3-minute walkthrough explaining the architecture, the tradeoff made, and future architectural improvements.

---

## ⏱️ Video Structure (Total: ~3:00)

### 0:00 – 0:45 | Introduction & Architecture Overview (45s)
- **Visual:** Show the running dashboard (`/`) and active terminal logs.
- **Script:**
  > *"Hi! I'm presenting my submission for the LinkPlease automation challenge. The core objective was to build a rock-solid Instagram Comment-to-DM engine on top of a hostile mock platform that rate limits to 10 requests per minute, redelivers 8% of events, randomly fails on 20% of requests, and fails 15% of accepted DMs during delivery.*
  > 
  > *I implemented all three tiers: Part A with atomic `(user_id, rule_id)` deduplication and crash-safe persistent SQLite queue; Part B with HMAC-SHA256 signature verification and live zero-inflation `/stats`; and Part C with background DM status reconciliation and proactive `comment.deleted` cancellation."*

---

### 0:45 – 1:50 | Question 1: One Tradeoff Made & What Was Given Up (65s)
- **Visual:** Highlight `database.py` (`user_rule_executions` UNIQUE constraint & WAL mode) and `rate_limiter.py`.
- **Script:**
  > *"Let's talk about the key tradeoff I made: **Strict Transactional Persistence vs. Raw In-Memory Throughput.**
  > 
  > *To guarantee that zero DMs are lost if the server process crashes or restarts, I chose SQLite in WAL mode with an atomic compound unique constraint on `(user_id, rule_id)`. Every event deduplication, job transition, and rule match happens inside an immediate disk transaction before the webhook responds 200.*
  > 
  > ***What I gave up by making this tradeoff:**
  > 1. **Throughput & Lock Contention:** Because SQLite serializes writes through a single file lock, absorbing a 500-event burst in 10 seconds introduces thread contention and requires configuring a generous `busy_timeout` (15s).
  > 2. **Queue Drain Latency:** Because we strictly enforce the upstream API's 10 req/min rate limit, sending 500 queued messages takes ~50 minutes of continuous pacing. An in-memory queue would have been slightly faster to ingest, but completely vulnerable to server restarts."*

---

### 1:50 – 2:40 | Question 2: What I'd Do Differently With One More Week (50s)
- **Visual:** Show `FAILURES.md` or a quick diagram of distributed architecture.
- **Script:**
  > *"With one more week, here is what I would improve:
  > 
  > 1. **Distributed Event Stream with Redis Streams & Celery/BullMQ:** Replace single-node SQLite with Redis Streams + PostgreSQL. This would allow horizontal scaling of webhook ingestion across multiple container instances without database file write locks.
  > 2. **Token Bucket Rate Limiting per Platform Account:** Implement distributed token buckets across Redis with atomic Lua scripts, ensuring multiple workers coordinate smoothly without exceeding the 10 req/min platform quota.
  > 3. **Dead Letter Queue (DLQ) & Admin Replay UI:** Build an automated DLQ dashboard for DMs that permanently fail after 5 retries, giving creators one-click manual retry or automated alert notifications."*

---

### 2:40 – 3:00 | Conclusion & Demo Wrap-up (20s)
- **Visual:** Show `/stats` endpoint returning clean, honest numbers.
- **Script:**
  > *"The system is fully tested with unit tests, simulated 500-event stress tests, and verified against ground truth logs. Thank you for your time and looking forward to discussing the code!"*
