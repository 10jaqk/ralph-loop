"""
Project Registry API

Admin-only API for managing project registry entries and database context access.

All endpoints require ADMIN_API_KEY authentication via Authorization header.
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import uuid
from datetime import datetime

from app.config import get_settings
from app.services.db_context_service import DBContextService, DBContextError
from app.models.project import SecretsProvider, DBContextMode

router = APIRouter(prefix="/projects", tags=["projects"])


# --- Pydantic Models ---

class ProjectCreate(BaseModel):
    """Request model for creating a project."""
    project_id: str = Field(..., max_length=64, description="Unique project identifier")
    name: str = Field(..., max_length=128, description="Project display name")
    repo_url: Optional[str] = Field(None, max_length=512, description="Git repository URL")
    default_branch: str = Field("main", max_length=64, description="Default git branch")
    secrets_provider: SecretsProvider = Field(SecretsProvider.RAILWAY, description="Secret provider")
    db_connection_ref: Optional[str] = Field(None, max_length=256, description="DB connection secret reference")
    db_context_mode: DBContextMode = Field(DBContextMode.METADATA_ONLY, description="DB access level")
    allowed_schemas: Optional[List[str]] = Field(None, description="Allowed database schemas")
    allowed_tables: Optional[List[str]] = Field(None, description="Allowed database tables")
    pii_fields: Optional[List[str]] = Field(None, description="PII field names for redaction")


class ProjectUpdate(BaseModel):
    """Request model for updating a project."""
    name: Optional[str] = Field(None, max_length=128)
    repo_url: Optional[str] = Field(None, max_length=512)
    default_branch: Optional[str] = Field(None, max_length=64)
    secrets_provider: Optional[SecretsProvider] = None
    db_connection_ref: Optional[str] = Field(None, max_length=256)
    db_context_mode: Optional[DBContextMode] = None
    allowed_schemas: Optional[List[str]] = None
    allowed_tables: Optional[List[str]] = None
    pii_fields: Optional[List[str]] = None


class ProjectResponse(BaseModel):
    """Response model for project data."""
    id: str
    project_id: str
    name: str
    repo_url: Optional[str]
    default_branch: str
    secrets_provider: SecretsProvider
    db_connection_ref: Optional[str]
    db_context_mode: DBContextMode
    allowed_schemas: Optional[List[str]]
    allowed_tables: Optional[List[str]]
    pii_fields: Optional[List[str]]
    created_by: str
    created_at: datetime
    updated_at: datetime


class SampleDataRequest(BaseModel):
    """Request model for sample data."""
    table_name: str = Field(..., description="Table name (schema.table or table)")
    limit: int = Field(10, ge=1, le=100, description="Max rows to return")
    build_id: Optional[str] = Field(None, description="Build ID for audit logging")


# --- Authentication ---

async def verify_admin_key(authorization: Optional[str] = Header(None)):
    """
    Verify admin API key from Authorization header.

    Format: "Bearer <ADMIN_API_KEY>"

    Raises:
        HTTPException: If authentication fails
    """
    settings = get_settings()

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization must be 'Bearer <token>'")

    token = authorization.split(" ", 1)[1]

    if token != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin API key")

    return True


# --- Database Dependency ---

async def get_db():
    """
    Get database connection.

    TODO: Replace with proper connection pool once FastAPI app is created.
    For now, this is a placeholder.
    """
    # This will be injected from FastAPI app's lifespan
    # For now, return None and handle in route
    return None


# --- Project CRUD Endpoints ---

@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    project: ProjectCreate,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Create a new project registry entry.

    Requires admin authentication.

    Example:
        POST /projects
        Authorization: Bearer <ADMIN_API_KEY>
        {
            "project_id": "kaiscout",
            "name": "KaiScout",
            "repo_url": "https://github.com/user/kaiscout",
            "secrets_provider": "railway",
            "db_connection_ref": "railway:KAISCOUT_DB_URL",
            "db_context_mode": "metadata_only",
            "allowed_schemas": ["public"],
            "pii_fields": ["email", "phone", "ssn"]
        }
    """
    # TODO: Implement with actual DB connection
    # For now, return mock response
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    List all project registry entries.

    Requires admin authentication.
    """
    # TODO: Implement with actual DB connection
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Get a specific project registry entry.

    Requires admin authentication.
    """
    # TODO: Implement with actual DB connection
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    updates: ProjectUpdate,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Update a project registry entry.

    Requires admin authentication.

    Only provided fields will be updated.
    """
    # TODO: Implement with actual DB connection
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Delete a project registry entry.

    Requires admin authentication.

    Cascades to all builds and reviews for this project.
    """
    # TODO: Implement with actual DB connection
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


# --- Database Context Endpoints ---

@router.get("/{project_id}/schema")
async def get_project_schema(
    project_id: str,
    build_id: Optional[str] = None,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Get database schema metadata for a project.

    Requires: db_context_mode >= metadata_only

    Returns schemas, tables, and columns.
    All access is audited.
    """
    # TODO: Implement with actual DB connection and DBContextService
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.get("/{project_id}/row-counts")
async def get_project_row_counts(
    project_id: str,
    build_id: Optional[str] = None,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Get row counts for all tables in a project database.

    Requires: db_context_mode >= metadata_only

    Returns dict mapping table names to row counts.
    All access is audited.
    """
    # TODO: Implement with actual DB connection and DBContextService
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.get("/{project_id}/migration-version")
async def get_project_migration_version(
    project_id: str,
    build_id: Optional[str] = None,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Get Alembic migration version for a project database.

    Requires: db_context_mode >= metadata_only

    Returns current migration version or null if not using Alembic.
    All access is audited.
    """
    # TODO: Implement with actual DB connection and DBContextService
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")


@router.post("/{project_id}/sample-data")
async def get_project_sample_data(
    project_id: str,
    request: SampleDataRequest,
    db=Depends(get_db),
    _: bool = Depends(verify_admin_key)
):
    """
    Get sample data from a project database table.

    Requires: db_context_mode = readonly

    PII fields are automatically redacted based on project configuration.
    All access is audited.

    Example:
        POST /projects/kaiscout/sample-data
        {
            "table_name": "public.users",
            "limit": 10,
            "build_id": "2026-01-11-abc123"
        }
    """
    # TODO: Implement with actual DB connection and DBContextService
    raise HTTPException(status_code=501, detail="Implementation pending - DB connection setup needed")
