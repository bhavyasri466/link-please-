import asyncio
import logging
import httpx
from typing import Optional
from app.config import settings
from app.database import (
    get_next_pending_job,
    update_job_accepted,
    update_job_delivered,
    update_job_failed,
    schedule_job_retry,
    get_reconciling_jobs,
    is_comment_deleted,
    record_stats_metric
)
from app.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

class BackgroundWorkerManager:
    def __init__(self):
        self.is_running = False
        self._send_task: Optional[asyncio.Task] = None
        self._reconcile_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def start(self):
        self.is_running = True
        self._http_client = httpx.AsyncClient(timeout=10.0)
        self._send_task = asyncio.create_task(self._send_loop(), name="dm_send_worker")
        self._reconcile_task = asyncio.create_task(self._reconcile_loop(), name="dm_reconcile_worker")
        logger.info("[Worker] Background send and reconciliation workers started.")

    async def stop(self):
        self.is_running = False
        if self._send_task:
            self._send_task.cancel()
        if self._reconcile_task:
            self._reconcile_task.cancel()
        if self._http_client:
            await self._http_client.aclose()
        logger.info("[Worker] Background workers stopped.")

    async def _send_loop(self):
        """Dispatches queued DMs strictly respecting outbound rate limits."""
        while self.is_running:
            try:
                job = get_next_pending_job()
                if not job:
                    await asyncio.sleep(0.3)
                    continue

                job_id = job["job_id"]
                comment_id = job["comment_id"]
                
                # Check if comment was deleted in the interim
                if is_comment_deleted(comment_id):
                    logger.info(f"[SendWorker] Comment {comment_id} was deleted before send. Cancelling job {job_id}.")
                    record_stats_metric("comment_deleted_cancelled", f"job={job_id}, comment={comment_id}")
                    update_job_failed(job_id, "comment_deleted_before_send")
                    continue

                # Rate limiting wait
                await rate_limiter.acquire()
                
                # Double-check deletion after rate limiter wait
                if is_comment_deleted(comment_id):
                    logger.info(f"[SendWorker] Comment {comment_id} was deleted during rate limit wait. Cancelling {job_id}.")
                    record_stats_metric("comment_deleted_cancelled", f"job={job_id}, comment={comment_id}")
                    update_job_failed(job_id, "comment_deleted_before_send")
                    continue

                url = f"{settings.PSEUDOGRAM_API_URL}/v1/dm/send"
                headers = {
                    "X-API-Key": settings.PSEUDOGRAM_API_KEY,
                    "Idempotency-Key": job["idempotency_key"],
                    "Content-Type": "application/json"
                }
                payload = {
                    "recipient_user_id": job["user_id"],
                    "message": job["message"],
                    "comment_id": comment_id
                }

                try:
                    resp = await self._http_client.post(url, json=payload, headers=headers)
                    status_code = resp.status_code

                    if status_code == 202:
                        # 202 Accepted -> status 'queued' on mock server -> we move to 'reconciling'
                        data = resp.json()
                        dm_id = data.get("dm_id")
                        logger.info(f"[SendWorker] DM accepted for job {job_id} -> dm_id={dm_id}")
                        update_job_accepted(job_id, dm_id)

                    elif status_code == 429:
                        # Rate limited
                        retry_after_hdr = resp.headers.get("Retry-After")
                        retry_after = float(retry_after_hdr) if retry_after_hdr else 60.0
                        logger.warning(f"[SendWorker] 429 Rate limited on job {job_id}. Retry-After: {retry_after}s")
                        rate_limiter.handle_429(retry_after)
                        schedule_job_retry(job_id, job["retry_count"], retry_after, "rate_limited_429")

                    elif status_code in (500, 502, 503, 504):
                        # Transient server error (~20% random on mock API) -> retry with exponential backoff
                        retries = job["retry_count"] + 1
                        if retries <= settings.MAX_RETRIES:
                            delay = settings.INITIAL_RETRY_BACKOFF_SECONDS * (2 ** (retries - 1))
                            logger.info(f"[SendWorker] 500 error on job {job_id}. Scheduling retry #{retries} in {delay:.1f}s")
                            schedule_job_retry(job_id, retries, delay, f"http_{status_code}_server_error")
                        else:
                            logger.error(f"[SendWorker] Job {job_id} exceeded max retries on 500 errors. Marking failed.")
                            update_job_failed(job_id, f"max_retries_exceeded_{status_code}")

                    elif status_code == 400:
                        # Malformed request - permanent failure
                        detail = resp.text
                        logger.error(f"[SendWorker] 400 Bad Request on job {job_id}: {detail}")
                        update_job_failed(job_id, f"bad_request_400: {detail}")

                    else:
                        logger.error(f"[SendWorker] Unexpected HTTP {status_code} on job {job_id}: {resp.text}")
                        update_job_failed(job_id, f"unexpected_status_{status_code}")

                except httpx.RequestError as exc:
                    # Network / transport errors
                    retries = job["retry_count"] + 1
                    if retries <= settings.MAX_RETRIES:
                        delay = settings.INITIAL_RETRY_BACKOFF_SECONDS * (2 ** (retries - 1))
                        logger.warning(f"[SendWorker] Network error ({exc}) on job {job_id}. Retry #{retries} in {delay:.1f}s")
                        schedule_job_retry(job_id, retries, delay, f"network_error: {str(exc)}")
                    else:
                        logger.error(f"[SendWorker] Network error limit reached on job {job_id}. Marking failed.")
                        update_job_failed(job_id, f"network_error_max_retries: {str(exc)}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[SendWorker] Unhandled error in send loop: {e}")
                await asyncio.sleep(1.0)

    async def _reconcile_loop(self):
        """Polls accepted DMs to reconcile terminal delivery status (delivered vs failed)."""
        while self.is_running:
            try:
                jobs = get_reconciling_jobs(limit=20)
                if not jobs:
                    await asyncio.sleep(settings.RECONCILIATION_INTERVAL_SECONDS)
                    continue

                for job in jobs:
                    if not self.is_running:
                        break

                    job_id = job["job_id"]
                    dm_id = job["dm_id"]
                    url = f"{settings.PSEUDOGRAM_API_URL}/v1/dm/{dm_id}"
                    headers = {"X-API-Key": settings.PSEUDOGRAM_API_KEY}

                    try:
                        resp = await self._http_client.get(url, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            dm_status = data.get("status")

                            if dm_status == "delivered":
                                logger.info(f"[ReconcileWorker] DM {dm_id} for job {job_id} confirmed DELIVERED.")
                                update_job_delivered(job_id)

                            elif dm_status == "failed":
                                # Accepted DM failed later on mock API (~15% of cases). Retry it!
                                retries = job["retry_count"] + 1
                                if retries <= settings.MAX_RETRIES:
                                    delay = settings.INITIAL_RETRY_BACKOFF_SECONDS * (2 ** (retries - 1))
                                    logger.warning(f"[ReconcileWorker] DM {dm_id} failed on platform! Retrying send (attempt #{retries}) in {delay:.1f}s")
                                    schedule_job_retry(job_id, retries, delay, "mock_platform_delivery_failed")
                                else:
                                    logger.error(f"[ReconcileWorker] DM {dm_id} failed on platform and exceeded max retries. Marking failed.")
                                    update_job_failed(job_id, "mock_platform_delivery_failed_max_retries")

                            elif dm_status == "queued":
                                # Still queued on upstream platform, wait for next cycle
                                pass

                        elif resp.status_code == 404:
                            # DM ID not found on mock API
                            logger.error(f"[ReconcileWorker] DM {dm_id} not found (404) for job {job_id}.")
                            update_job_failed(job_id, "dm_id_not_found_404")

                        elif resp.status_code >= 500:
                            # Transient 500 on status read -> simply try again on next polling loop
                            logger.warning(f"[ReconcileWorker] Transient 500 reading status of dm_id {dm_id}.")

                    except httpx.RequestError as exc:
                        logger.warning(f"[ReconcileWorker] Network error checking dm_id {dm_id}: {exc}")

                    # Small delay between status checks to be polite
                    await asyncio.sleep(0.2)

                await asyncio.sleep(settings.RECONCILIATION_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[ReconcileWorker] Unhandled error in reconcile loop: {e}")
                await asyncio.sleep(2.0)

worker_manager = BackgroundWorkerManager()
