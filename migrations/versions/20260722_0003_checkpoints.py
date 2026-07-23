"""Add versioned checkpoint commits.

Revision ID: 20260722_0003
Revises: 20260722_0002
Create Date: 2026-07-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0003"
down_revision: Union[str, None] = "20260722_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "checkpoint_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "plan_steps",
        sa.Column("checkpoint_sequence", sa.Integer(), nullable=True),
    )
    op.create_table(
        "checkpoints",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "step_id"],
            ["plan_steps.run_id", "plan_steps.step_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "sequence"),
    )
    op.create_index(
        "ix_checkpoints_run_step",
        "checkpoints",
        ["run_id", "step_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_checkpoints_run_step", table_name="checkpoints")
    op.drop_table("checkpoints")
    op.drop_column("plan_steps", "checkpoint_sequence")
    op.drop_column("runs", "checkpoint_sequence")

