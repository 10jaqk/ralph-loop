"""
Review Dispatcher Service

Fetches pending reviews and dispatches them to GPT respecting 4/hour rate limit.

Runs as scheduled job (every 5 minutes).

Dispatch methods:
1. MCP Poll (current): Mark as DISPATCHED, GPT polls via get_latest_ready_build
2. ChatGPT API (future): Call ChatGPT API directly with structured prompt
3. Webhook (future): POST notification to webhook that GPT monitors
"""

import asyncpg
import logging
from typing import List, Dict, Optional
from datetime import datetime
import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""
    pass


class ReviewDispatcher:
    """
    Review dispatcher with rate limiting.

    Enforces 4 reviews/hour global limit across all review types.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        """
        Initialize dispatcher.

        Args:
            db_pool: Database connection pool
        """
        self.db_pool = db_pool
        self.settings = get_settings()
        self.redis_client: Optional[aioredis.Redis] = None

    async def _init_redis(self):
        """Initialize Redis client for rate limiting."""
        if self.redis_client is None and self.settings.REDIS_URL:
            try:
                self.redis_client = await aioredis.from_url(
                    self.settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True
                )
                logger.info("Redis client initialized for rate limiting")
            except Exception as e:
                logger.warning(f"Failed to initialize Redis client: {e}. Rate limiting will be disabled.")
                self.redis_client = None

    async def _check_rate_limit(self) -> bool:
        """
        Check if we can dispatch a review (respects 4/hour rate limit).

        Uses Redis token bucket with 4 tokens, refill rate of 4/hour.

        Returns:
            True if dispatch allowed, False if rate limited

        Raises:
            None (fails open if Redis unavailable)
        """
        await self._init_redis()

        if not self.redis_client:
            # Fail open if Redis unavailable
            logger.warning("Redis unavailable, rate limiting disabled (fail open)")
            return True

        try:
            key = "ralph:review_dispatch:rate_limit"
            now = datetime.now().timestamp()

            # Token bucket parameters
            capacity = self.settings.REVIEW_RATE_LIMIT  # 4 tokens
            refill_rate = self.settings.REVIEW_RATE_LIMIT / self.settings.REVIEW_RATE_WINDOW  # 4/3600 = 0.00111 tokens/sec

            # Get current bucket state
            bucket_data = await self.redis_client.get(key)

            if bucket_data:
                tokens, last_update = bucket_data.split(":")
                tokens = float(tokens)
                last_update = float(last_update)
            else:
                # Initialize bucket
                tokens = float(capacity)
                last_update = now

            # Refill tokens based on time elapsed
            elapsed = now - last_update
            tokens = min(capacity, tokens + (elapsed * refill_rate))

            # Check if we can consume 1 token
            if tokens >= 1.0:
                # Consume token
                tokens -= 1.0
                await self.redis_client.set(key, f"{tokens}:{now}", ex=7200)  # 2-hour TTL
                return True
            else:
                # Rate limited
                logger.warning(f"Rate limit exceeded: {tokens:.2f} tokens available (need 1.0)")
                return False

        except Exception as e:
            logger.error(f"Rate limit check failed: {e}. Failing open.")
            return True  # Fail open

    async def _fetch_pending_reviews(self, limit: int = 10) -> List[Dict]:
        """
        Fetch pending reviews from queue.

        Args:
            limit: Maximum reviews to fetch

        Returns:
            List of pending review dicts
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    rq.id,
                    rq.build_pk,
                    rq.build_id,
                    rq.project_id,
                    rq.task_id,
                    rq.queue_type,
                    rq.priority,
                    rb.build_type,
                    rb.review_bundle,
                    rb.diff_unified,
                    rb.test_exit_code,
                    rb.lint_exit_code,
                    rq.created_at
                FROM ralph_review_queue rq
                JOIN ralph_builds rb ON rq.build_pk = rb.id
                WHERE rq.status = 'PENDING'
                ORDER BY rq.priority DESC, rq.created_at ASC
                LIMIT $1
            """, limit)

            return [dict(row) for row in rows]

    async def _dispatch_review(self, review: Dict) -> bool:
        """
        Dispatch a single review to GPT.

        Current implementation: Mark as DISPATCHED (GPT polls via MCP).

        Future implementations:
        - Call ChatGPT API with structured prompt
        - POST webhook notification

        Args:
            review: Review queue entry

        Returns:
            True if dispatched successfully, False otherwise
        """
        try:
            async with self.db_pool.acquire() as conn:
                # Mark as dispatched
                await conn.execute("""
                    UPDATE ralph_review_queue
                    SET status = 'DISPATCHED', dispatched_at = NOW(), updated_at = NOW()
                    WHERE id = $1
                """, review['id'])

                # Log dispatch event
                await conn.execute("""
                    INSERT INTO ralph_review_dispatches (
                        review_queue_pk, build_id, inspector_model, dispatch_method,
                        api_response_code, api_response_body
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                    review['id'],
                    review['build_id'],
                    'gpt-5.2',  # Default inspector model
                    'mcp_poll',
                    200,  # Success
                    'dispatched'
                )

                logger.info(
                    f"Dispatched review for build {review['build_id']} "
                    f"(project={review['project_id']}, queue_type={review['queue_type']})"
                )
                return True

        except Exception as e:
            logger.error(f"Failed to dispatch review {review['id']}: {e}")

            try:
                # Mark as failed
                async with self.db_pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE ralph_review_queue
                        SET status = 'FAILED', error_message = $1, updated_at = NOW()
                        WHERE id = $2
                    """, str(e), review['id'])

                    # Log failed dispatch
                    await conn.execute("""
                        INSERT INTO ralph_review_dispatches (
                            review_queue_pk, build_id, inspector_model, dispatch_method,
                            error_type, api_response_body
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                        review['id'],
                        review['build_id'],
                        'gpt-5.2',
                        'mcp_poll',
                        'dispatch_error',
                        str(e)
                    )
            except Exception as log_err:
                logger.error(f"Failed to log dispatch error: {log_err}")

            return False

    async def dispatch_pending_reviews(self, batch_size: int = 10) -> Dict[str, int]:
        """
        Dispatch pending reviews respecting rate limit.

        Args:
            batch_size: Maximum reviews to fetch per run

        Returns:
            Stats dict with dispatched, failed, rate_limited counts
        """
        logger.info("Starting review dispatch cycle...")

        # Fetch pending reviews
        pending_reviews = await self._fetch_pending_reviews(limit=batch_size)

        if not pending_reviews:
            logger.info("No pending reviews")
            return {"dispatched": 0, "failed": 0, "rate_limited": 0}

        logger.info(f"Found {len(pending_reviews)} pending reviews")

        dispatched = 0
        failed = 0
        rate_limited = 0

        for review in pending_reviews:
            # Check rate limit
            can_dispatch = await self._check_rate_limit()

            if not can_dispatch:
                logger.warning("Rate limit hit, stopping dispatch cycle")
                rate_limited = len(pending_reviews) - (dispatched + failed)
                break

            # Dispatch review
            success = await self._dispatch_review(review)

            if success:
                dispatched += 1
            else:
                failed += 1

        logger.info(
            f"Dispatch cycle complete: {dispatched} dispatched, {failed} failed, "
            f"{rate_limited} rate limited, {len(pending_reviews)} total"
        )

        return {
            "dispatched": dispatched,
            "failed": failed,
            "rate_limited": rate_limited
        }


async def enqueue_review(
    db_pool: asyncpg.Pool,
    build_pk: str,
    build_id: str,
    project_id: str,
    task_id: Optional[str],
    queue_type: str,
    priority: int = 5
):
    """
    Enqueue a review request.

    Deduplication: If pending review exists for (project, task, queue_type), replace it.

    Args:
        db_pool: Database connection pool
        build_pk: Build UUID (internal PK)
        build_id: Build ID (human-readable)
        project_id: Project identifier
        task_id: Task identifier
        queue_type: 'PLAN' or 'CODE'
        priority: Priority (1-10, higher = more urgent)
    """
    async with db_pool.acquire() as conn:
        # Delete old pending reviews for same (project, task, queue_type)
        if task_id:
            await conn.execute("""
                DELETE FROM ralph_review_queue
                WHERE project_id = $1
                  AND task_id = $2
                  AND queue_type = $3
                  AND status = 'PENDING'
            """, project_id, task_id, queue_type)

        # Insert new review request
        await conn.execute("""
            INSERT INTO ralph_review_queue (
                build_pk, build_id, project_id, task_id, queue_type, priority, status
            ) VALUES ($1, $2, $3, $4, $5, $6, 'PENDING')
        """, build_pk, build_id, project_id, task_id, queue_type, priority)

        logger.info(
            f"Enqueued {queue_type} review for build {build_id} "
            f"(task={task_id}, priority={priority})"
        )
