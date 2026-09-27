"""agent definition rework: model_binding, assignments

Revision ID: b7c3f1a2d9e4
Revises: 4bf075ee2ede
Create Date: 2026-09-27

SQLite table rebuilds run via batch_alter_table. Two batch contexts (add,
then drop) bracket the data copy: batch queues its ops until context exit,
so the copy cannot be interleaved inside one batch.
"""
from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c3f1a2d9e4'
down_revision: str | None = '4bf075ee2ede'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('agents') as batch_op:
        batch_op.add_column(sa.Column('model_binding', sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'assignments',
                sa.JSON(),
                nullable=False,
                server_default=sa.text("('{}')"),
            )
        )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, model_tag FROM agents WHERE model_tag IS NOT NULL")
    ).all()
    for row in rows:
        bind.execute(
            sa.text("UPDATE agents SET model_binding = :binding WHERE id = :id"),
            {"binding": json.dumps({"kind": "tag", "tag": row.model_tag}), "id": row.id},
        )
    with op.batch_alter_table('agents') as batch_op:
        batch_op.drop_column('model_tag')


def downgrade() -> None:
    with op.batch_alter_table('agents') as batch_op:
        batch_op.add_column(sa.Column('model_tag', sa.Text(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, model_binding FROM agents")).all()
    for row in rows:
        if not row.model_binding:
            continue
        binding = json.loads(row.model_binding)
        if binding.get("kind") == "tag":
            bind.execute(
                sa.text("UPDATE agents SET model_tag = :tag WHERE id = :id"),
                {"tag": binding.get("tag"), "id": row.id},
            )
    with op.batch_alter_table('agents') as batch_op:
        batch_op.drop_column('assignments')
        batch_op.drop_column('model_binding')
