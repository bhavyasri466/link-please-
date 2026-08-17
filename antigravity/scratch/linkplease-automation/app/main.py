import logging
import time
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request, HTTPException, status, Header
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.database import (
    init_db,
    create_rule,
    get_all_rules,
    record_event_if_new,
    upsert_comment,
    mark_comment_deleted,
    try_record_user_rule_and_enqueue_dm,
    record_stats_metric,
    get_stats_summary,
    get_recent_jobs
)
from app.security import verify_webhook_signature
from app.worker import worker_manager
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("linkplease")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and background workers
    logger.info("Initializing LinkPlease database...")
    init_db()
    logger.info("Starting background worker threads...")
    await worker_manager.start()
    yield
    # Shutdown: stop workers gracefully
    logger.info("Stopping background workers...")
    await worker_manager.stop()

app = FastAPI(
    title="LinkPlease Automation Service",
    description="High-reliability Instagram Comment-to-DM automation engine with hostile mock API resilience.",
    version="1.0.0",
    lifespan=lifespan
)

# ----------------- Request / Response Models -----------------

class RuleCreateRequest(BaseModel):
    keyword: str = Field(..., min_length=1, description="Keyword to match in comments (case-insensitive)")
    dm_message: str = Field(..., min_length=1, description="Message text to DM the commenter")

class RuleResponse(BaseModel):
    rule_id: str
    keyword: str
    dm_message: str

class StatsResponse(BaseModel):
    sent: int
    failed: int
    queued: int
    duplicates_blocked: int

# ----------------- Mandatory Required Endpoints -----------------

@app.post(
    "/rules",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an automation rule"
)
async def api_create_rule(req: RuleCreateRequest):
    """
    Creates a new rule: when a comment contains `keyword` (case-insensitive),
    send `dm_message` to the commenter.
    """
    rule = create_rule(keyword=req.keyword.strip(), dm_message=req.dm_message.strip())
    return RuleResponse(
        rule_id=rule["rule_id"],
        keyword=rule["keyword"],
        dm_message=rule["dm_message"]
    )

@app.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Ingest incoming Instagram comment events"
)
async def api_webhook(
    request: Request,
    x_pseudogram_signature: Optional[str] = Header(None, alias="X-PseudoGram-Signature")
):
    """
    Receives comment events from the mock API.
    Must return 200 within 5 seconds.
    Verifies HMAC-SHA256 signature when secret is configured.
    Handles duplicate events, comment.deleted, rule matching, and atomic deduplication.
    """
    raw_body = await request.body()
    
    # 1. Verify Signature (Part B)
    if settings.PSEUDOGRAM_API_KEY and x_pseudogram_signature:
        if not verify_webhook_signature(raw_body, x_pseudogram_signature):
            logger.warning("Rejected webhook request due to invalid HMAC signature.")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    elif settings.PSEUDOGRAM_API_KEY and settings.VERIFY_SIGNATURE and not x_pseudogram_signature:
        logger.warning("Rejected webhook request: missing X-PseudoGram-Signature header.")
        raise HTTPException(status_code=401, detail="Missing X-PseudoGram-Signature header")

    # 2. Parse JSON payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Malformed JSON in webhook: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    data = payload.get("data", {})

    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Missing event_id or event_type")

    # 3. Deduplicate by event_id (~8% of events redelivered)
    is_new_event = record_event_if_new(event_id, event_type)
    if not is_new_event:
        logger.info(f"Duplicate event_id received: {event_id}. Blocking duplicate.")
        record_stats_metric("duplicate_event", f"event_id={event_id}")
        return {"status": "ok", "message": "duplicate event ignored"}

    # 4. Handle comment.deleted events (Part C)
    if event_type == "comment.deleted":
        comment_id = data.get("comment_id")
        if comment_id:
            cancelled_count = mark_comment_deleted(comment_id)
            logger.info(f"Handled comment.deleted for {comment_id}. Cancelled {cancelled_count} pending DMs.")
        return {"status": "ok"}

    # 5. Handle comment.created events
    if event_type == "comment.created":
        comment_id = data.get("comment_id")
        post_id = data.get("post_id")
        text = data.get("text", "")
        created_at = data.get("created_at")
        from_user = data.get("from", {})
        user_id = from_user.get("user_id")

        if not comment_id or not user_id:
            logger.warning(f"comment.created event missing comment_id or user_id: {data}")
            return {"status": "ok"}

        # Store comment record
        upsert_comment(comment_id, post_id, user_id, text, created_at)

        # Match against all registered rules (case-insensitive search anywhere in comment text)
        rules = get_all_rules()
        normalized_text = text.lower() if text else ""

        for rule in rules:
            keyword = rule["keyword"].lower()
            if keyword in normalized_text:
                # Attempt atomic user-rule execution & enqueue DM
                job_id = try_record_user_rule_and_enqueue_dm(
                    user_id=user_id,
                    rule_id=rule["rule_id"],
                    comment_id=comment_id,
                    message=rule["dm_message"]
                )
                if job_id:
                    logger.info(f"Enqueued DM job {job_id} for user={user_id}, rule={rule['rule_id']} (matched '{rule['keyword']}')")
                else:
                    logger.info(f"User {user_id} already received DM for rule {rule['rule_id']}. Blocked duplicate.")

        return {"status": "ok"}

    # Catch-all for unknown event types
    logger.info(f"Received unrecognized event_type: {event_type}")
    return {"status": "ok"}

@app.get(
    "/stats",
    response_model=StatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get real-time live performance statistics"
)
async def api_get_stats():
    """
    Returns exact real-time live numbers:
    - sent: DMs the mock API confirmed as delivered
    - failed: DMs given up after max retries
    - queued: DMs waiting to send or waiting on retry / reconciliation
    - duplicates_blocked: DMs correctly chosen not to send
    """
    stats = get_stats_summary()
    return StatsResponse(**stats)

# ----------------- Observability & Helper Endpoints -----------------

@app.get("/rules", response_model=List[Dict[str, Any]], summary="List all registered automation rules")
async def api_list_rules():
    return get_all_rules()

@app.get("/jobs", response_model=List[Dict[str, Any]], summary="List recent DM jobs")
async def api_list_jobs(limit: int = 100):
    return get_recent_jobs(limit=limit)

@app.get("/health", summary="Health check")
async def api_health():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "workers_running": worker_manager.is_running,
        "api_key_configured": bool(settings.PSEUDOGRAM_API_KEY)
    }

@app.post("/simulate/trigger", summary="Trigger simulation run on Mock API")
async def api_trigger_simulation(webhook_url: str, count: int = 500, duration_seconds: int = 10):
    if not settings.PSEUDOGRAM_API_KEY:
        raise HTTPException(status_code=400, detail="PSEUDOGRAM_API_KEY not configured")
    
    url = f"{settings.PSEUDOGRAM_API_URL}/v1/simulate/start"
    headers = {"X-API-Key": settings.PSEUDOGRAM_API_KEY, "Content-Type": "application/json"}
    payload = {"webhook_url": webhook_url, "count": count, "duration_seconds": duration_seconds}
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()

@app.get("/simulate/truth/{run_id}", summary="Fetch ground truth for simulation run")
async def api_fetch_truth(run_id: str):
    if not settings.PSEUDOGRAM_API_KEY:
        raise HTTPException(status_code=400, detail="PSEUDOGRAM_API_KEY not configured")
        
    url = f"{settings.PSEUDOGRAM_API_URL}/v1/simulate/{run_id}/truth"
    headers = {"X-API-Key": settings.PSEUDOGRAM_API_KEY}
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()

# Mount static files and dashboard
import os
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "LinkPlease Automation API is running. Visit /docs for Swagger UI."}
