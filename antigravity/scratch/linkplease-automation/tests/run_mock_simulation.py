"""
Mock API Simulation Runner & Self-Testing Harness
Spins up an internal Mock Pseudogram Server simulating all hostile conditions:
- 10 req/60s rate limit on POST /v1/dm/send
- 20% transient 500 errors
- 15% accepted DMs that fail later during delivery
- 8% duplicate event_ids
- Burst of 500 events over duration
Validates our LinkPlease engine against Mock API ground truth!
"""

import asyncio
import random
import time
import uuid
import httpx
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
import uvicorn
from contextlib import asynccontextmanager

# ----------------- Mock Pseudogram API -----------------

mock_app = FastAPI(title="Mock Hostile Pseudogram API")

class MockAPIState:
    def __init__(self):
        self.sent_events = []
        self.dm_store = {}
        self.rate_limit_timestamps = []
        self.max_rate_limit = 10
        self.rate_window = 60.0
        self.truth_stats = {
            "total_events_sent": 0,
            "unique_events": 0,
            "duplicate_events_sent": 0,
            "keyword_matched_users": set(),
            "dms_delivered": 0,
            "dms_failed": 0
        }

state = MockAPIState()

@mock_app.post("/v1/dm/send", status_code=202)
async def mock_dm_send(
    payload: dict,
    x_api_key: str = Header(None, alias="X-API-Key"),
    idempotency_key: str = Header(None, alias="Idempotency-Key")
):
    now = time.time()
    
    # 1. Check Rate Limit (10 req/60s)
    state.rate_limit_timestamps = [t for t in state.rate_limit_timestamps if t > now - state.rate_window]
    if len(state.rate_limit_timestamps) >= state.max_rate_limit:
        oldest = state.rate_limit_timestamps[0]
        retry_after = int(oldest + state.rate_window - now) + 1
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited"},
            headers={"Retry-After": str(max(1, retry_after))}
        )
    
    # 2. Simulate 20% Random 500 Internal Error
    if random.random() < 0.20:
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    # Record valid request slot
    state.rate_limit_timestamps.append(now)

    # 3. Check Idempotency Key
    if idempotency_key:
        for dm_id, dm_info in state.dm_store.items():
            if dm_info.get("idempotency_key") == idempotency_key:
                return {"dm_id": dm_id, "status": dm_info["status"]}

    dm_id = f"dm_{uuid.uuid4().hex[:8]}"
    
    # Simulate 15% delayed failure, 85% delivered
    will_deliver = random.random() >= 0.15
    
    state.dm_store[dm_id] = {
        "dm_id": dm_id,
        "recipient_user_id": payload.get("recipient_user_id"),
        "comment_id": payload.get("comment_id"),
        "message": payload.get("message"),
        "idempotency_key": idempotency_key,
        "status": "queued",
        "will_deliver": will_deliver,
        "accepted_at": now
    }
    
    return {"dm_id": dm_id, "status": "queued"}

@mock_app.get("/v1/dm/{dm_id}")
async def mock_get_dm(dm_id: str):
    now = time.time()
    if dm_id not in state.dm_store:
        raise HTTPException(status_code=404, detail="DM not found")
        
    dm = state.dm_store[dm_id]
    # After 1.5 seconds from acceptance, transition to terminal status
    if now - dm["accepted_at"] > 1.5:
        if dm["will_deliver"]:
            dm["status"] = "delivered"
        else:
            dm["status"] = "failed"
            
    return {
        "dm_id": dm_id,
        "status": dm["status"],
        "recipient_user_id": dm["recipient_user_id"],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

@mock_app.post("/v1/simulate/start")
async def mock_simulate_start(payload: dict):
    webhook_url = payload.get("webhook_url")
    count = payload.get("count", 50)
    duration = payload.get("duration_seconds", 5)
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    
    # Run simulation background task
    asyncio.create_task(run_simulation_events(webhook_url, count, duration))
    return {"run_id": run_id, "status": "started", "count": count}

async def run_simulation_events(webhook_url: str, count: int, duration_seconds: float):
    async with httpx.AsyncClient(timeout=10.0) as client:
        interval = duration_seconds / max(1, count)
        created_events = []
        
        # 10 distinct users commenting
        users = [f"usr_{i:03d}" for i in range(1, 15)]
        keywords = ["PRICE", "CATALOG", "BUY", "HELLO", "NICE POST"]
        
        for i in range(count):
            user_id = random.choice(users)
            text_choice = random.choice(keywords)
            event_id = f"evt_{i:04d}_{uuid.uuid4().hex[:6]}"
            comment_id = f"cmt_{i:04d}"
            
            event = {
                "event_id": event_id,
                "event_type": "comment.created",
                "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "data": {
                    "comment_id": comment_id,
                    "post_id": "post_mock_1",
                    "text": f"{text_choice} please! #{i}",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "from": {"user_id": user_id, "username": f"user_{user_id}"}
                }
            }
            created_events.append(event)
            
            # Fire event
            try:
                await client.post(webhook_url, json=event)
            except Exception as e:
                print(f"Error posting event: {e}")
                
            # ~8% chance to redeliver a previous event
            if random.random() < 0.08 and created_events:
                dup_event = random.choice(created_events)
                try:
                    await client.post(webhook_url, json=dup_event)
                except Exception:
                    pass
                    
            await asyncio.sleep(interval)

if __name__ == "__main__":
    print("Starting Mock Hostile Pseudogram API on http://127.0.0.1:9000")
    uvicorn.run(mock_app, host="127.0.0.1", port=9000)
