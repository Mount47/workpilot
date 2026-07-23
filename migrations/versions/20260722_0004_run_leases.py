"""Add fenced Run leases.

Revision ID: 20260722_0004
Revises: 20260722_0003
Create Date: 2026-07-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0004"
down_revision: Union[str, None] = "20260722_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "execution_attempt",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_runs_state_lease_expires_at",
        "runs",
        ["state", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_runs_state_lease_expires_at", table_name="runs")
    op.drop_column("runs", "execution_attempt")
    op.drop_column("runs", "lease_expires_at")
    op.drop_column("runs", "lease_owner")
