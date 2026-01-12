"""
Ralph Loop - Main FastAPI Application

Standalone AI development pipeline infrastructure.

Services:
- Project Registry API (admin-only)
- Build Ingestion API (for builders like Claude Code)
- Review Queue & Dispatcher (automatic GPT reviews)
- MCP Server (HTTP/SSE for ChatGPT integration)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncpg
import logging

from app.config import get_settings
from app.api import projects, builds
from app.mcp import server as mcp_server
from app.services.scheduler import RalphScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

settings = get_settings()


# --- Database Connection Pool ---

db_pool: asyncpg.Pool = None
ralph_scheduler: RalphScheduler = None


async def init_db_pool():
    """Initialize database connection pool."""
    global db_pool

    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL not configured")

    logger.info("Initializing database connection pool...")

    db_pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60
    )

    logger.info("Database connection pool ready")


async def close_db_pool():
    """Close database connection pool."""
    global db_pool

    if db_pool:
        logger.info("Closing database connection pool...")
        await db_pool.close()
        logger.info("Database connection pool closed")


# --- FastAPI Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Handles startup and shutdown tasks.
    """
    global ralph_scheduler

    # Startup
    logger.info(f"Ralph Loop starting (ENV={settings.ENV})...")
    await init_db_pool()

    # Start scheduler
    ralph_scheduler = RalphScheduler(db_pool)
    ralph_scheduler.start()

    yield

    # Shutdown
    logger.info("Ralph Loop shutting down...")

    # Stop scheduler
    if ralph_scheduler:
        ralph_scheduler.shutdown()

    await close_db_pool()


# --- FastAPI App ---

app = FastAPI(
    title="Ralph Loop",
    description="Autonomous AI development pipeline with two-gate review system",
    version="1.0.0",
    lifespan=lifespan
)


# --- CORS Middleware ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health Check ---

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway."""
    return {
        "status": "healthy",
        "service": "ralph-loop",
        "version": "1.0.0",
        "env": settings.ENV
    }


@app.get("/")
async def root():
    """Root endpoint with service information."""
    return {
        "service": "Ralph Loop",
        "description": "Autonomous AI development pipeline",
        "version": "1.0.0",
        "env": settings.ENV,
        "endpoints": {
            "health": "/health",
            "projects": "/projects (admin-only)",
            "builds": "/builds",
            "mcp_sse": "/mcp/sse",
            "mcp_tools": "/mcp/tools/list"
        }
    }


# --- API Routers ---

app.include_router(projects.router)
app.include_router(builds.router)
app.include_router(mcp_server.router)


# --- Database Dependency Override ---

async def get_db_connection():
    """
    Get database connection from pool.

    Yields a connection that will be automatically returned to pool.
    """
    global db_pool

    if not db_pool:
        raise RuntimeError("Database pool not initialized")

    async with db_pool.acquire() as conn:
        yield conn


# Override the get_db dependency in routers
projects.get_db = get_db_connection
builds.get_db = get_db_connection


# --- Development Server ---

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        reload=(settings.ENV == "development"),
        log_level="info"
    )
