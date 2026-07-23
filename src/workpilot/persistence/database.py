"""SQLAlchemy metadata and engine/session construction for persistence."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    """Declarative base exported to Alembic."""


class RunRow(Base):
    """SQL representation of durable API Run metadata."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_state_lease_expires_at", "state", "lease_expires_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    workspace: Mapped[str] = mapped_column(String(512), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    output_dir: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    request_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    checkpoint_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlanStepRow(Base):
    """SQL representation of one validated PlanStep and its execution state."""

    __tablename__ = "plan_steps"

    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    step_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dependencies: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False)
    success_rule_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    success_evaluation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checkpoint_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ToolCallRow(Base):
    """SQL representation of one content-minimized logical ToolCall."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "step_id"],
            ["plan_steps.run_id", "plan_steps.step_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "run_id",
            "step_id",
            "execution_no",
            name="uq_tool_calls_logical_execution",
        ),
    )

    tool_call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_no: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    physical_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ArtifactMetadataRow(Base):
    """SQL representation of versioned file Artifact metadata."""

    __tablename__ = "artifact_metadata"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "name",
            "version",
            name="uq_artifact_metadata_version",
        ),
    )

    artifact_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CheckpointRow(Base):
    """SQL metadata for one file checkpoint committed with a PlanStep."""

    __tablename__ = "checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "step_id"],
            ["plan_steps.run_id", "plan_steps.step_id"],
            ondelete="CASCADE",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def create_database_engine(database_url: str) -> Engine:
    """Create a synchronous engine without exposing the URL in application logs."""
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker:
    """Create short-lived, thread-safe-by-construction SQLAlchemy sessions."""
    return sessionmaker(bind=engine, expire_on_commit=False)
