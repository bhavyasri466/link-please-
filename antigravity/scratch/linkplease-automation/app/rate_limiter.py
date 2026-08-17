import asyncio
import time
import logging
from collections import deque
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)

class OutboundRateLimiter:
    """
    Sliding window & token-paced rate limiter for outbound mock API calls.
    Enforces <= 10 requests per rolling 60s window.
    Dynamically adjusts if 429 Retry-After is encountered.
    """
    def __init__(
        self,
        max_requests: int = settings.RATE_LIMIT_MAX_REQUESTS,
        window_seconds: float = settings.RATE_LIMIT_WINDOW_SECONDS,
        min_interval_seconds: float = settings.MIN_DISPATCH_INTERVAL_SECONDS
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.min_interval_seconds = min_interval_seconds
        self.timestamps: deque = deque()
        self.last_dispatched_at: float = 0.0
        self.paused_until: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until a request slot is available within the rolling window and pacing limits."""
        while True:
            async with self._lock:
                now = time.time()
                
                # Check if we are paused due to a 429 Retry-After
                if now < self.paused_until:
                    wait_time = self.paused_until - now
                    logger.warning(f"[RateLimiter] Rate limited by upstream API. Pausing for {wait_time:.2f}s...")
                else:
                    # Purge timestamps older than the rolling window
                    while self.timestamps and self.timestamps[0] <= now - self.window_seconds:
                        self.timestamps.popleft()
                        
                    # Check window capacity
                    if len(self.timestamps) < self.max_requests:
                        # Also check min interval pacing
                        time_since_last = now - self.last_dispatched_at
                        if time_since_last >= self.min_interval_seconds:
                            # Token acquired!
                            self.timestamps.append(now)
                            self.last_dispatched_at = now
                            return
                        else:
                            wait_time = self.min_interval_seconds - time_since_last
                    else:
                        # Window full: wait until oldest timestamp exits the window
                        oldest = self.timestamps[0]
                        wait_time = (oldest + self.window_seconds) - now + 0.1

            # Sleep outside the lock so other tasks aren't blocked from checking status
            await asyncio.sleep(max(0.05, wait_time))

    def handle_429(self, retry_after_seconds: Optional[float] = None):
        """Called when upstream API responds with HTTP 429."""
        backoff = retry_after_seconds if (retry_after_seconds and retry_after_seconds > 0) else 60.0
        self.paused_until = time.time() + backoff
        logger.warning(f"[RateLimiter] Received 429 Rate Limit! Pausing outbound dispatcher until {self.paused_until} ({backoff}s)")

rate_limiter = OutboundRateLimiter()
