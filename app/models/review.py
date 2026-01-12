"""
Review Queue and Inspection Models

Manages the review queue, inspection verdicts, and revision requests.
"""

from sqlalchemy import Column, String, Integer, Boolean, Float, Text, DateTime, Enum as SQLEnum, Index, ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
import enum
import uuid

Base = declarative_base()


class ReviewQueueType(enum.Enum):
    """Review queue type."""
    PLAN = "PLAN"
    CODE = "CODE"


class ReviewQueueStatus(enum.Enum):
    """Review queue status."""
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RevisionStatus(enum.Enum):
    """Revision status."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class RalphReviewQueue(Base):
    """
    Review queue table.

    Manages pending reviews with deduplication (only latest per project/task/type).
    """
    __tablename__ = "ralph_review_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_type = Column(SQLEnum(ReviewQueueType), nullable=False, index=True)
    build_pk = Column(UUID(as_uuid=True), ForeignKey('ralph_builds.id', ondelete='CASCADE'), nullable=False)
    build_id = Column(String(128), nullable=False)
    project_id = Column(String(64), nullable=False, index=True)
    task_id = Column(String(128), nullable=True, index=True)
    priority = Column(Integer, nullable=False, server_default="5")  # 1-10, higher = more urgent
    status = Column(
        SQLEnum(ReviewQueueStatus),
        nullable=False,
        server_default=ReviewQueueStatus.PENDING.value,
        index=True
    )
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<RalphReviewQueue {self.build_id} ({self.queue_type.value}, {self.status.value})>"


# Unique constraint: only one pending review per (project, task, queue_type)
Index(
    'ix_ralph_review_queue_unique_pending',
    RalphReviewQueue.project_id,
    RalphReviewQueue.task_id,
    RalphReviewQueue.queue_type,
    RalphReviewQueue.status,
    unique=True,
    postgresql_where=text("status = 'PENDING'")
)

# Index for dispatcher queries
Index(
    'ix_ralph_review_queue_dispatch',
    RalphReviewQueue.queue_type,
    RalphReviewQueue.status,
    RalphReviewQueue.priority,
    RalphReviewQueue.created_at
)


class RalphReviewDispatch(Base):
    """
    Review dispatch log.

    Tracks each dispatch attempt to GPT.
    """
    __tablename__ = "ralph_review_dispatches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_queue_pk = Column(UUID(as_uuid=True), ForeignKey('ralph_review_queue.id', ondelete='CASCADE'), nullable=False)
    build_id = Column(String(128), nullable=False)
    inspector_model = Column(String(64), nullable=False)
    dispatch_method = Column(String(32), nullable=False)  # 'api', 'mcp', 'webhook'
    api_response_code = Column(Integer, nullable=True)
    api_response_body = Column(Text, nullable=True)
    error_type = Column(String(64), nullable=True)  # 'rate_limit', 'timeout', 'api_error'
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<RalphReviewDispatch {self.build_id} ({self.inspector_model})>"


class RalphInspection(Base):
    """
    Inspection verdict table.

    Stores GPT's inspection results (PASS/FAIL with issues).
    Idempotent: one inspection per (build, inspector_model).
    """
    __tablename__ = "ralph_inspections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    build_pk = Column(UUID(as_uuid=True), ForeignKey('ralph_builds.id', ondelete='CASCADE'), nullable=False)
    build_id = Column(String(128), nullable=False)
    inspector_model = Column(String(64), nullable=False)
    passed = Column(Boolean, nullable=False)
    issues = Column(JSONB, nullable=True)  # Structured issue list
    suggestions = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    raw_response = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint('build_pk', 'inspector_model', name='uq_ralph_inspections_build_inspector'),
    )

    def __repr__(self):
        return f"<RalphInspection {self.build_id} ({'PASS' if self.passed else 'FAIL'})>"


class RalphRevision(Base):
    """
    Revision request table.

    Stores structured feedback when inspection fails.
    """
    __tablename__ = "ralph_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    build_pk = Column(UUID(as_uuid=True), ForeignKey('ralph_builds.id', ondelete='CASCADE'), nullable=False)
    build_id = Column(String(128), nullable=False)
    revision_id = Column(String(128), unique=True, nullable=False)
    feedback_summary = Column(Text, nullable=False)
    priority_fixes = Column(JSONB, nullable=False)
    patch_guidance = Column(Text, nullable=True)
    do_not_change = Column(JSONB, nullable=True)
    status = Column(
        SQLEnum(RevisionStatus),
        nullable=False,
        server_default=RevisionStatus.PENDING.value
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<RalphRevision {self.build_id} ({self.status.value})>"


class RalphDBAccessLog(Base):
    """
    Database access audit log.

    Tracks every project DB access for security audit.
    """
    __tablename__ = "ralph_db_access_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(64), nullable=False, index=True)
    build_id = Column(String(128), nullable=True)
    access_mode = Column(String(32), nullable=False)  # 'metadata', 'readonly'
    query_count = Column(Integer, nullable=False)
    row_count = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<RalphDBAccessLog {self.project_id} ({self.access_mode})>"


# Index for audit queries
Index('ix_ralph_db_access_log_project_time', RalphDBAccessLog.project_id, RalphDBAccessLog.created_at)
