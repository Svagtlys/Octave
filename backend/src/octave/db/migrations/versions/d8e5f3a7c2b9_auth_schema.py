"""auth schema: user credentials, roles, auth_sessions + pre-auth backfill

Revision ID: d8e5f3a7c2b9
Revises: c9d4e2f6a1b8
Create Date: 2026-10-05

Backfill for installs that already have ``users`` rows without credentials
(the spec's migration-safety plan): usernames are slugged from
``display_name`` with ``.2``/``.3`` dedup, the oldest user becomes the single
owner, everyone is active, password hashes are set to random unknowable
values (login stays blocked until ``reset-password``), and missing
Participant rows are created. Data steps are hand-written — autogenerate
cannot reason about them.
"""
import re
import secrets
import uuid
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# Autogenerate renders the UTCDateTime TypeDecorator by qualified name.
import octave.db.models.base  # noqa: F401


revision: str = 'd8e5f3a7c2b9'
down_revision: str | None = 'c9d4e2f6a1b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _slugify(display_name: str) -> str:
    """Username slug per the auth spec: lowercase, ``[^a-z0-9._-]`` -> ``-``,
    collapse repeats, clamp to 2-32 chars, ``user`` when empty."""
    slug = re.sub(r"[^a-z0-9._-]", "-", display_name.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    slug = slug[:32].strip("-")
    if len(slug) < 2:
        slug = "user" if not slug else f"user-{slug}"
    return slug


def _dedupe(candidates: list[str]) -> list[str]:
    """Make slugs unique: first occurrence keeps its slug, later ones get
    ``.2``, ``.3``, ... suffixes (the OIDC JIT collision convention)."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for slug in candidates:
        if slug in seen:
            seen[slug] += 1
            candidate = f"{slug}.{seen[slug]}"
            while candidate in seen:
                seen[slug] += 1
                candidate = f"{slug}.{seen[slug]}"
            seen[candidate] = 1
            result.append(candidate)
        else:
            seen[slug] = 1
            result.append(slug)
    return result


def upgrade() -> None:
    # 1. Add columns nullable/defaults-without-semantics so existing rows
    #    survive the ALTER. NOT NULL on username is enforced after backfill.
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('username', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('password_hash', sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column('role', sa.Text(), nullable=False,
                      server_default=sa.text("'member'"))
        )
        batch_op.add_column(
            sa.Column('status', sa.Text(), nullable=False,
                      server_default=sa.text("'active'"))
        )
        batch_op.add_column(
            sa.Column('last_login_at', octave.db.models.base.UTCDateTime(),
                      nullable=True)
        )

    bind = op.get_bind()

    # 2. Usernames from display_name slugs, deduplicated, oldest first.
    users = bind.execute(
        sa.text("SELECT id, display_name FROM users ORDER BY created_at ASC, id ASC")
    ).all()
    usernames = _dedupe([_slugify(row.display_name) for row in users])
    for row, username in zip(users, usernames, strict=True):
        bind.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :id"),
            {"username": username, "id": row.id},
        )

    # 3. Oldest user (by created_at) is the single owner.
    bind.execute(
        sa.text("UPDATE users SET role = 'owner' WHERE id = "
                "(SELECT id FROM users ORDER BY created_at ASC, id ASC LIMIT 1)")
    )

    # 4. Random unknowable password hashes: nobody is silently locked in,
    #    nobody can log in until ``reset-password`` runs.
    for row in bind.execute(
        sa.text("SELECT id FROM users WHERE password_hash IS NULL")
    ).all():
        bind.execute(
            sa.text("UPDATE users SET password_hash = :h WHERE id = :id"),
            {"h": "!" + secrets.token_urlsafe(32), "id": row.id},
        )

    # 5. Every user gets a Participant row (users author events through
    #    participants; a user without one is a user that cannot speak).
    user_ids = {row.id for row in users}
    existing = {
        row.user_id
        for row in bind.execute(
            sa.text("SELECT user_id FROM participants WHERE user_id IS NOT NULL")
        ).all()
    }
    for user_id in user_ids - existing:
        bind.execute(
            sa.text("INSERT INTO participants (id, user_id, agent_id, label) "
                    "VALUES (:id, :user_id, NULL, "
                    "(SELECT display_name FROM users WHERE id = :user_id))"),
            {"id": uuid.uuid4().hex, "user_id": user_id},
        )

    # 6. Enforce username NOT NULL + UNIQUE via batch rebuild. The template
    #    reflects step 1's interim schema; queued ops render the final one.
    meta = sa.MetaData()
    interim = sa.Table(
        'users', meta,
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('username', sa.Text(), nullable=True),
        sa.Column('password_hash', sa.Text(), nullable=True),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False,
                  server_default=sa.text("'member'")),
        sa.Column('status', sa.Text(), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column('last_login_at', octave.db.models.base.UTCDateTime(),
                  nullable=True),
        sa.Column('created_at', octave.db.models.base.UTCDateTime(),
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('users', copy_from=interim) as batch_op:
        batch_op.alter_column('username', nullable=False)
        batch_op.create_unique_constraint('uq_users_username', ['username'])

    op.create_table(
        'auth_sessions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('created_at', octave.db.models.base.UTCDateTime(), nullable=False),
        sa.Column('expires_at', octave.db.models.base.UTCDateTime(), nullable=False),
        sa.Column('last_seen_at', octave.db.models.base.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.create_index('ix_auth_sessions_user_id', ['user_id'], unique=False)
        batch_op.create_index('ix_auth_sessions_expires_at', ['expires_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('auth_sessions', schema=None) as batch_op:
        batch_op.drop_index('ix_auth_sessions_expires_at')
        batch_op.drop_index('ix_auth_sessions_user_id')
    op.drop_table('auth_sessions')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_constraint('uq_users_username', type_='unique')
        batch_op.drop_column('last_login_at')
        batch_op.drop_column('status')
        batch_op.drop_column('role')
        batch_op.drop_column('password_hash')
        batch_op.drop_column('username')
