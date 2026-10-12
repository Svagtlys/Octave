"""The engine-creation choke point (issue #27 review).

Engines built without explicit-BEGIN transaction control silently
reintroduce the RELEASE-SAVEPOINT-commits bug. Guards:
1. behavioral — savepoints through ``create_sqlite_engine`` nest and roll
   back without committing (the connection-level assertion), and
2. structural — ``octave.`` source may only create SQLite engines through
   the choke point (AST scan, same pattern as the SDK-quarantine guard).
"""

import ast
import asyncio
from pathlib import Path

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base, Event, Session, User

# Modules allowed to call create_async_engine: the choke point itself.
_ALLOWED_MODULES = {"_bootstrap.py"}


@pytest_asyncio.fixture
async def choke_engine(tmp_path):  # type: ignore[no-untyped-def]
    eng = create_sqlite_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def test_savepoint_does_not_commit_on_choke_point_engine(
    choke_engine: AsyncEngine,
) -> None:
    """RELEASE SAVEPOINT must not commit: the caller's transaction boundary
    survives a nested savepoint's release and an outer rollback."""
    factory = async_sessionmaker(choke_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(User(id="u_guard", username="guard", display_name="Guard"))
        session.add(
            Session(id="s_guard", created_by_user_id="u_guard", status="active")
        )
        await session.commit()
    async with factory() as session:
        async with session.begin_nested():
            session.add(
                Event(
                    id="e_guard",
                    session_id="s_guard",
                    seq=1,
                    kind="system",
                    payload={},
                )
            )
            await session.flush()
        await session.rollback()
    async with factory() as checker:
        count = await checker.scalar(
            select(func.count()).select_from(Event).where(
                Event.session_id == "s_guard"
            )
        )
    assert count == 0


def test_src_creates_sqlite_engines_only_via_choke_point() -> None:
    """No octave.* module may call create_async_engine directly — engines
    must come from create_sqlite_engine / SqliteVecAdapter.make_engine."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "octave"
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name in _ALLOWED_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "name", None) or getattr(
                    func, "attr", None
                )
                if name == "create_async_engine":
                    offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], (
        "SQLite engines must be created via octave.db._bootstrap."
        f"create_sqlite_engine: {offenders}"
    )


async def test_concurrent_sessions_do_not_share_transactions(
    tmp_path: Path,
) -> None:
    """Concurrent requests must not collide on one DBAPI connection.

    The engine's real URL is the bare ``sqlite+aiosqlite://`` scheme (the
    path lives in the async_creator), so SQLAlchemy's pool heuristic reads
    it as an in-memory database and hands back StaticPool — every session
    then shares one connection, and the explicit-BEGIN listener fires
    ``BEGIN`` inside a peer's open transaction ("cannot start a
    transaction within a transaction"). That killed parallel page-load
    requests against the live server; each checkout needs its own
    connection (WAL + busy_timeout cover cross-connection contention).
    """
    eng = create_sqlite_engine(f"sqlite:///{tmp_path / 'conc.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)

    async def one() -> int:
        async with factory() as session:
            result = await session.execute(select(func.count()).select_from(User))
            return result.scalar_one()

    counts = await asyncio.gather(*(one() for _ in range(6)))
    assert counts == [0] * 6
    await eng.dispose()
