"""Out-of-band session slide (issue #161): owns its short transaction,
awaits with a timeout, never fails the caller."""

import sqlite3
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.auth.service import AuthService
from octave.auth.sliding import slide_session
from octave.auth.store import AuthStore
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base
from octave.db.models.auth import AuthSession
from octave.db.models.base import utcnow


async def _factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'slide.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _login(factory: async_sessionmaker[AsyncSession]) -> str:
    async with factory() as session:
        store = AuthStore(session)
        await store.create_user(
            username="alice", display_name="Alice", password_hash="dummy$pw"
        )
        await session.commit()
        service = AuthService(
            store=store, hasher=DummyHasher(), settings=AuthSettings()
        )
        token = await service.login("alice", "pw")
        await session.commit()
        return token


async def test_slide_applies_out_of_band(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    now = utcnow()
    # age the row past the grace window
    async with factory() as session:
        row = await session.scalar(select(AuthSession))
        assert row is not None
        row.last_seen_at = now - timedelta(minutes=5)
        await session.commit()
    assert await slide_session(factory, token, settings=AuthSettings(), now=now) is True
    async with factory() as session:
        row = await session.scalar(select(AuthSession))
        assert row is not None and row.last_seen_at == now


async def test_slide_throttled_returns_false(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    assert (
        await slide_session(factory, token, settings=AuthSettings(), now=utcnow())
    ) is False


async def test_slide_never_raises_on_gone_db(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    # A path whose parent directory does not exist: every connect errors out.
    dead = async_sessionmaker(
        create_sqlite_engine(f"sqlite:///{tmp_path / 'missing' / 'x.db'}")
    )
    assert (
        await slide_session(dead, token, settings=AuthSettings(), now=utcnow())
    ) is False


async def test_slide_timeout_cancels_without_raising(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    # Hold the write lock from a raw connection beyond the slide timeout.
    # The engine URL carries no path (the async_creator owns it), so derive
    # it from the fixture's own filename.
    db_path = str(tmp_path / "slide.db")
    raw = sqlite3.connect(db_path)
    raw.isolation_level = None
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE auth_sessions SET last_seen_at = last_seen_at")
    try:
        settings = AuthSettings(touch_timeout_seconds=0.1)
        assert (
            await slide_session(factory, token, settings=settings, now=utcnow())
        ) is False
    finally:
        raw.rollback()
        raw.close()


async def test_slide_missing_factory_returns_false() -> None:
    assert (
        await slide_session(None, "token", settings=AuthSettings(), now=utcnow())
    ) is False
