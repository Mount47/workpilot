"""Create durable API runs table.

Revision ID: 20260722_0001
Revises:
Create Date: 2026-07-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260722_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("workspace", sa.String(length=512), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("output_dir", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_runs_created_at", "runs", ["created_at"], unique=False)
    op.create_index("ix_runs_state", "runs", ["state"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_runs_state", table_name="runs")
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_table("runs")
