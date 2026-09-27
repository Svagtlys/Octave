"""agent instances table

Revision ID: c9d4e2f6a1b8
Revises: b7c3f1a2d9e4
Create Date: 2026-09-27
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# Autogenerate renders the UTCDateTime TypeDecorator by qualified name.
import octave.db.models.base  # noqa: F401


revision: str = 'c9d4e2f6a1b8'
down_revision: str | None = 'b7c3f1a2d9e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agent_instances',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('agent_id', sa.Text(), nullable=False),
    sa.Column('session_id', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', octave.db.models.base.UTCDateTime(), nullable=False),
    sa.Column('updated_at', octave.db.models.base.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('agent_id', 'session_id', name='uq_agent_instances_agent_session')
    )


def downgrade() -> None:
    op.drop_table('agent_instances')
