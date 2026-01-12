"""
Project Registry Model

Stores project metadata and DB connection references (not raw credentials).
"""

from sqlalchemy import Column, String, DateTime, Enum as SQLEnum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
import enum
import uuid

Base = declarative_base()


class SecretsProvider(enum.Enum):
    """Secret storage providers."""
    RAILWAY = "railway"
    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    GCP_SECRET_MANAGER = "gcp_secret_manager"
    VAULT = "vault"
    NONE = "none"


class DBContextMode(enum.Enum):
    """Database context access levels."""
    NONE = "none"
    METADATA_ONLY = "metadata_only"
    READONLY = "readonly"


class ProjectRegistry(Base):
    """
    Project registry table.

    Stores references to project databases (not raw credentials).
    Admin-only create/update/delete operations.
    """
    __tablename__ = "project_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(128), nullable=False)
    repo_url = Column(String(512), nullable=True)
    default_branch = Column(String(64), nullable=False, server_default="main")

    # Secret management
    secrets_provider = Column(
        SQLEnum(SecretsProvider),
        nullable=False,
        server_default=SecretsProvider.RAILWAY.value
    )
    db_connection_ref = Column(String(256), nullable=True)  # Reference, not raw credential

    # DB context security
    db_context_mode = Column(
        SQLEnum(DBContextMode),
        nullable=False,
        server_default=DBContextMode.METADATA_ONLY.value
    )
    allowed_schemas = Column(JSONB, nullable=True)  # ["public", "analytics"]
    allowed_tables = Column(JSONB, nullable=True)  # Optional whitelist for sample data
    pii_fields = Column(JSONB, nullable=True)  # Fields to redact (e.g., ["email", "ssn"])

    # Audit
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<ProjectRegistry {self.project_id}>"
