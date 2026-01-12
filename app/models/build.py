"""
Ralph Build Model

Stores build artifacts from ANY project (tagged by project_id).
"""

from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, Enum as SQLEnum, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
import enum
import uuid

Base = declarative_base()


class BuildType(enum.Enum):
    """Build classification."""
    PLAN = "PLAN"
    CODE = "CODE"


class BuilderSignal(enum.Enum):
    """Builder completion signal."""
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    NEEDS_WORK = "NEEDS_WORK"
    DEPLOYED = "DEPLOYED"


class InspectionStatus(enum.Enum):
    """Inspection status."""
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class RalphBuild(Base):
    """
    Build artifact table.

    Stores builds from ALL projects, tagged by project_id.
    PLAN builds: architecture/design artifacts only.
    CODE builds: implementation + tests + traceability.
    """
    __tablename__ = "ralph_builds"

    # Internal PK
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Human-readable ID
    build_id = Column(String(128), unique=True, nullable=False, index=True)

    # Project reference
    project_id = Column(String(64), nullable=False, index=True)

    # Build classification
    build_type = Column(
        SQLEnum(BuildType),
        nullable=False,
        server_default=BuildType.CODE.value
    )
    task_id = Column(String(128), nullable=True, index=True)  # Group related builds
    task_description = Column(Text, nullable=True)
    plan_build_id = Column(String(128), nullable=True)  # CODE builds reference their PLAN

    # Source control
    commit_sha = Column(String(64), nullable=False)
    branch = Column(String(128), nullable=False)
    changed_files = Column(JSONB, nullable=True)
    diff_unified = Column(Text, nullable=True)
    diff_source = Column(String(16), nullable=False, server_default="agent")  # 'agent' or 'github'

    # Review artifacts
    review_bundle = Column(JSONB, nullable=True)  # Structured artifacts (REVIEW_INTENT, ADR, etc.)

    # Test results (CODE builds only)
    test_command = Column(String(256), nullable=True)
    test_exit_code = Column(Integer, nullable=True)
    test_output_tail = Column(Text, nullable=True)
    coverage = Column(JSONB, nullable=True)

    # Lint results (CODE builds only)
    lint_command = Column(String(256), nullable=True)
    lint_exit_code = Column(Integer, nullable=True)
    lint_output_tail = Column(Text, nullable=True)

    # Builder metadata
    builder_signal = Column(
        SQLEnum(BuilderSignal),
        nullable=False,
        server_default=BuilderSignal.READY_FOR_REVIEW.value
    )
    builder_notes = Column(JSONB, nullable=True)

    # Inspection status
    inspection_status = Column(
        SQLEnum(InspectionStatus),
        nullable=False,
        server_default=InspectionStatus.PENDING.value,
        index=True
    )
    iteration_count = Column(Integer, nullable=False, server_default="1")
    iteration_logs = Column(JSONB, nullable=True)

    # Guardrails
    requires_human_approval = Column(Boolean, nullable=False, server_default="false")
    approval_reason = Column(String(256), nullable=True)
    human_approved_by = Column(String(128), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<RalphBuild {self.build_id} ({self.build_type.value})>"


# Index for project queries
Index('ix_ralph_builds_project', RalphBuild.project_id, RalphBuild.created_at)
