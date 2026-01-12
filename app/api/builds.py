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
import json

from app.config import get_settings
from app.services.telegram_service import get_telegram_service

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
    # Generate build_id
    timestamp = datetime.now().isoformat()
    short_sha = artifact.commit_sha[:7] if artifact.commit_sha else "unknown"
    build_id = f"{timestamp}-{short_sha}"

    # Check guardrails
    requires_approval, approval_reason = check_requires_approval(
        artifact.changed_files,
        artifact.diff_unified
    )

    # Insert build into database
    build_uuid = uuid.uuid4()

    await db.execute("""
        INSERT INTO ralph_builds (
            id, build_id, project_id, build_type, task_id, task_description,
            plan_build_id, commit_sha, branch, changed_files, diff_unified,
            diff_source, review_bundle, test_command, test_exit_code,
            test_output_tail, coverage, lint_command, lint_exit_code,
            lint_output_tail, builder_signal, builder_notes,
            inspection_status, iteration_count, requires_human_approval,
            approval_reason
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13::jsonb,
            $14, $15, $16, $17::jsonb, $18, $19, $20, $21, $22::jsonb, $23, $24,
            $25, $26
        )
    """,
        build_uuid,
        build_id,
        artifact.project_id,
        artifact.build_type,
        artifact.task_id,
        artifact.task_description,
        artifact.plan_build_id,
        artifact.commit_sha,
        artifact.branch,
        json.dumps(artifact.changed_files) if artifact.changed_files else None,
        artifact.diff_unified,
        artifact.diff_source,
        json.dumps(artifact.review_bundle) if artifact.review_bundle else None,
        artifact.test_command,
        artifact.test_exit_code,
        artifact.test_output_tail,
        json.dumps(artifact.coverage) if artifact.coverage else None,
        artifact.lint_command,
        artifact.lint_exit_code,
        artifact.lint_output_tail,
        artifact.builder_signal,
        json.dumps(artifact.builder_notes) if artifact.builder_notes else None,
        "PENDING",  # inspection_status
        1,  # iteration_count
        requires_approval,
        approval_reason
    )

    # Send Telegram notification if requires approval
    if requires_approval:
        telegram = get_telegram_service()
        await telegram.send_approval_request(
            build_id=build_id,
            project_id=artifact.project_id,
            reason=approval_reason,
            changed_files=artifact.changed_files or [],
            test_passed=(artifact.test_exit_code == 0) if artifact.test_exit_code is not None else True,
            lint_passed=(artifact.lint_exit_code == 0) if artifact.lint_exit_code is not None else True
        )
    else:
        # Send status update for non-approval builds
        telegram = get_telegram_service()
        await telegram.send_status_update(
            build_id=build_id,
            project_id=artifact.project_id,
            status="submitted",
            message=f"📤 *New build submitted*\n\nTests: {'✅' if artifact.test_exit_code == 0 else '❌'}\nLint: {'✅' if artifact.lint_exit_code == 0 else '❌'}\n\n🔍 ChatGPT will inspect soon..."
        )

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
    row = await db.fetchrow(
        "SELECT * FROM ralph_builds WHERE build_id = $1",
        build_id
    )

    if not row:
        raise HTTPException(status_code=404, detail=f"Build '{build_id}' not found")

    return {
        "id": str(row['id']),
        "build_id": row['build_id'],
        "project_id": row['project_id'],
        "build_type": row['build_type'],
        "task_id": row['task_id'],
        "task_description": row['task_description'],
        "plan_build_id": row['plan_build_id'],
        "commit_sha": row['commit_sha'],
        "branch": row['branch'],
        "changed_files": json.loads(row['changed_files']) if row['changed_files'] else None,
        "diff_unified": row['diff_unified'],
        "diff_source": row['diff_source'],
        "review_bundle": json.loads(row['review_bundle']) if row['review_bundle'] else None,
        "test_command": row['test_command'],
        "test_exit_code": row['test_exit_code'],
        "test_output_tail": row['test_output_tail'],
        "coverage": json.loads(row['coverage']) if row['coverage'] else None,
        "lint_command": row['lint_command'],
        "lint_exit_code": row['lint_exit_code'],
        "lint_output_tail": row['lint_output_tail'],
        "builder_signal": row['builder_signal'],
        "builder_notes": json.loads(row['builder_notes']) if row['builder_notes'] else None,
        "inspection_status": row['inspection_status'],
        "iteration_count": row['iteration_count'],
        "iteration_logs": json.loads(row['iteration_logs']) if row['iteration_logs'] else None,
        "requires_human_approval": row['requires_human_approval'],
        "approval_reason": row['approval_reason'],
        "human_approved_by": row['human_approved_by'],
        "created_at": row['created_at'].isoformat() if row['created_at'] else None,
        "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None
    }


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
