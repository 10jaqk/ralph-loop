"""
Build Ingestion API

Accepts build artifacts from Claude Code and other builders.

Endpoints:
- POST /builds/ingest - Submit a build artifact
- POST /builds/test-ingest - Test endpoint for development (less strict validation)
- GET /builds/{build_id} - Get build details
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid
import hashlib

from app.config import get_settings

router = APIRouter(prefix="/builds", tags=["builds"])


# --- Pydantic Models ---

class BuildArtifact(BaseModel):
    """Build artifact submitted by builder (Claude Code, etc.)."""

    # Project identification
    project_id: str = Field(..., max_length=64, description="Project identifier")

    # Build classification
    build_type: str = Field("CODE", description="Build type: PLAN or CODE")
    task_id: Optional[str] = Field(None, max_length=128, description="Task identifier (groups related builds)")
    task_description: Optional[str] = Field(None, description="High-level task goal")
    plan_build_id: Optional[str] = Field(None, max_length=128, description="Referenced PLAN build (for CODE builds)")

    # Git context
    commit_sha: str = Field(..., max_length=64, description="Git commit SHA")
    branch: str = Field(..., max_length=128, description="Git branch")

    # Changes
    changed_files: Optional[List[str]] = Field(None, description="List of changed file paths")
    diff_unified: Optional[str] = Field(None, description="Unified diff")
    diff_source: str = Field("agent", max_length=16, description="Diff source: agent or github")

    # Review artifacts (for PLAN builds)
    review_bundle: Optional[Dict[str, str]] = Field(
        None,
        description="Review artifacts (REVIEW_INTENT, ADR, ARCH_OVERVIEW, RISK_REGISTER, etc.)"
    )

    # Test results (for CODE builds)
    test_command: Optional[str] = Field(None, max_length=256)
    test_exit_code: Optional[int] = None
    test_output_tail: Optional[str] = None
    coverage: Optional[Dict[str, Any]] = None

    # Lint results (for CODE builds)
    lint_command: Optional[str] = Field(None, max_length=256)
    lint_exit_code: Optional[int] = None
    lint_output_tail: Optional[str] = None

    # Builder metadata
    builder_signal: str = Field("READY_FOR_REVIEW", description="READY_FOR_REVIEW, NEEDS_WORK, or DEPLOYED")
    builder_notes: Optional[Dict[str, Any]] = Field(
        None,
        description="Builder notes (assumptions, low_confidence_areas, etc.)"
    )


class BuildIngestResponse(BaseModel):
    """Response from build ingestion."""
    status: str
    build_id: str
    inspection_status: str
    review_queued: bool
    requires_human_approval: bool
    approval_reason: Optional[str] = None


# --- Guardrails ---

FORBIDDEN_PATHS = [
    "backend/app/core/security",
    "backend/app/services/billing",
    "backend/app/core/config.py",
    ".env",
    "secrets",
]

DEPENDENCY_FILES = [
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
]


def check_requires_approval(changed_files: Optional[List[str]], diff: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Check if build requires human approval based on guardrails.

    Args:
        changed_files: List of changed file paths
        diff: Unified diff

    Returns:
        Tuple of (requires_approval, reason)
    """
    if not changed_files:
        return False, None

    reasons = []

    # Check forbidden paths
    for forbidden in FORBIDDEN_PATHS:
        if any(forbidden in f for f in changed_files):
            reasons.append(f"Protected area: {forbidden}")

    # Check dependency changes
    for dep_file in DEPENDENCY_FILES:
        if any(dep_file in f for f in changed_files):
            reasons.append(f"Dependency change: {dep_file}")

    if reasons:
        return True, "; ".join(reasons)

    return False, None


# --- Database Dependency ---

async def get_db():
    """
    Get database connection.

    TODO: Replace with proper connection pool once FastAPI app is created.
    """
    return None


# --- Build Ingestion Endpoints ---

@router.post("/ingest", response_model=BuildIngestResponse, status_code=201)
async def ingest_build(
    artifact: BuildArtifact,
    db=Depends(get_db)
):
    """
    Ingest a build artifact.

    Creates build record, applies guardrails, and auto-enqueues for review.

    Example:
        POST /builds/ingest
        {
            "project_id": "kaiscout",
            "build_type": "CODE",
            "task_id": "feat-user-api",
            "task_description": "Build user management API",
            "plan_build_id": "2026-01-11T10:00:00Z-plan-abc",
            "commit_sha": "abc123def456",
            "branch": "feat-user-api",
            "changed_files": ["backend/app/api/users.py", "backend/tests/test_users.py"],
            "diff_unified": "...",
            "test_command": "pytest backend/tests/",
            "test_exit_code": 0,
            "test_output_tail": "23 passed in 1.2s",
            "coverage": {"pct": 85.2},
            "lint_exit_code": 0,
            "builder_signal": "READY_FOR_REVIEW",
            "builder_notes": {
                "assumptions": ["Used existing auth middleware"],
                "low_confidence_areas": ["Permission checking edge cases"]
            }
        }
    """
    # TODO: Implement with actual DB connection
    # For now, return mock response

    # Generate build_id
    timestamp = datetime.now().isoformat()
    short_sha = artifact.commit_sha[:7] if artifact.commit_sha else "unknown"
    build_id = f"{timestamp}-{short_sha}"

    # Check guardrails
    requires_approval, approval_reason = check_requires_approval(
        artifact.changed_files,
        artifact.diff_unified
    )

    # Mock response
    return BuildIngestResponse(
        status="ingested",
        build_id=build_id,
        inspection_status="PENDING",
        review_queued=(artifact.builder_signal == "READY_FOR_REVIEW"),
        requires_human_approval=requires_approval,
        approval_reason=approval_reason
    )


@router.post("/test-ingest", response_model=BuildIngestResponse, status_code=201)
async def test_ingest_build(
    artifact: BuildArtifact,
    db=Depends(get_db)
):
    """
    Test endpoint for build ingestion (development only).

    Same as /ingest but with relaxed validation for testing.

    IMPORTANT: This endpoint should be disabled in production.
    """
    settings = get_settings()

    if settings.ENV == "production":
        raise HTTPException(
            status_code=403,
            detail="Test ingestion endpoint is disabled in production. Use /builds/ingest instead."
        )

    # Delegate to main ingestion (for now same implementation)
    return await ingest_build(artifact, db)


@router.get("/{build_id}")
async def get_build(
    build_id: str,
    db=Depends(get_db)
):
    """
    Get build details by ID.

    Returns full build artifact with inspection status.
    """
    # TODO: Implement with actual DB connection
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.get("/{build_id}/inspection")
async def get_build_inspection(
    build_id: str,
    db=Depends(get_db)
):
    """
    Get inspection verdict for a build.

    Returns inspection results (passed, issues, suggestions, confidence).
    """
    # TODO: Implement with actual DB connection
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.get("/{build_id}/revisions")
async def get_build_revisions(
    build_id: str,
    db=Depends(get_db)
):
    """
    Get revision requests for a build.

    Returns structured feedback when inspection fails.
    """
    # TODO: Implement with actual DB connection
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")
