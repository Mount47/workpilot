"""Create durable execution ledger.

Revision ID: 20260722_0002
Revises: 20260722_0001
Create Date: 2026-07-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260722_0002"
down_revision: Union[str, None] = "20260722_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_runs_idempotency_key_hash",
        "runs",
        ["idempotency_key_hash"],
        unique=True,
    )

    op.create_table(
        "plan_steps",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.String(length=128), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=False),
        sa.Column("success_rule_ids", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_type", sa.String(length=128), nullable=True),
        sa.Column("success_evaluation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "step_id"),
    )
    op.create_index("ix_plan_steps_status", "plan_steps", ["status"], unique=False)

    op.create_table(
        "tool_calls",
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("execution_no", sa.Integer(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("physical_attempts", sa.Integer(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "step_id"],
            ["plan_steps.run_id", "plan_steps.step_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tool_call_id"),
        sa.UniqueConstraint(
            "run_id",
            "step_id",
            "execution_no",
            name="uq_tool_calls_logical_execution",
        ),
    )
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"], unique=False)
    op.create_index("ix_tool_calls_state", "tool_calls", ["state"], unique=False)

    op.create_table(
        "artifact_metadata",
        sa.Column("artifact_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint(
            "run_id",
            "name",
            "version",
            name="uq_artifact_metadata_version",
        ),
    )
    op.create_index(
        "ix_artifact_metadata_run_id",
        "artifact_metadata",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_metadata_run_id", table_name="artifact_metadata")
    op.drop_table("artifact_metadata")
    op.drop_index("ix_tool_calls_state", table_name="tool_calls")
    op.drop_index("ix_tool_calls_run_id", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_plan_steps_status", table_name="plan_steps")
    op.drop_table("plan_steps")
    op.drop_index("ix_runs_idempotency_key_hash", table_name="runs")
    op.drop_column("runs", "request_fingerprint")
    op.drop_column("runs", "idempotency_key_hash")
