"""mcp server headers column

Revision ID: f2a6c8d0e4b7
Revises: d8e5f3a7c2b9
Create Date: 2026-11-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a6c8d0e4b7'
down_revision: str | None = 'd8e5f3a7c2b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'mcp_servers',
        sa.Column('headers', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('mcp_servers', 'headers')
