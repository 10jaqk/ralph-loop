"""
Scheduler Service

Runs scheduled background jobs using APScheduler.

Jobs:
- Review Dispatcher: Every 5 minutes, dispatches pending reviews to GPT
"""

import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import asyncpg

from app.services.review_dispatcher import ReviewDispatcher
from app.config import get_settings

logger = logging.getLogger(__name__)


class RalphScheduler:
    """
    Ralph Loop scheduler.

    Manages background jobs with APScheduler.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        """
        Initialize scheduler.

        Args:
            db_pool: Database connection pool
        """
        self.db_pool = db_pool
        self.settings = get_settings()
        self.scheduler = AsyncIOScheduler()
        self.dispatcher = ReviewDispatcher(db_pool)

    async def _run_review_dispatcher(self):
        """
        Run review dispatcher job.

        Fetches and dispatches pending reviews.
        """
        try:
            logger.info("Review dispatcher job started")
            stats = await self.dispatcher.dispatch_pending_reviews(batch_size=10)
            logger.info(f"Review dispatcher job completed: {stats}")
        except Exception as e:
            logger.error(f"Review dispatcher job failed: {e}", exc_info=True)

    def start(self):
        """
        Start the scheduler.

        Adds all scheduled jobs and starts the scheduler.
        """
        logger.info("Starting Ralph scheduler...")

        # Add review dispatcher job (every 5 minutes)
        self.scheduler.add_job(
            self._run_review_dispatcher,
            trigger=IntervalTrigger(minutes=5),
            id='review_dispatcher',
            name='Review Dispatcher',
            max_instances=1,  # Only one instance at a time
            replace_existing=True
        )

        # Start scheduler
        self.scheduler.start()
        logger.info("Ralph scheduler started")

    def shutdown(self):
        """
        Shutdown the scheduler.

        Waits for running jobs to complete before shutting down.
        """
        logger.info("Shutting down Ralph scheduler...")
        self.scheduler.shutdown(wait=True)
        logger.info("Ralph scheduler shutdown complete")
