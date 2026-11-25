"""Issue #161 repro: concurrent authed reads must never 500 while other
connections commit. Pre-fix: the in-band touch upgrades the deferred read
transaction and dies with sqlite3.OperationalError: database is locked
(SQLITE_BUSY_SNAPSHOT). Post-fix: the request path is write-free.

In-scope contract: the 25 concurrent authed READS all succeed (200) while
writes commit in parallel. Writer-vs-writer SQLITE_BUSY/BUSY_SNAPSHOT on
POST /sessions is the known residual (the route-level snapshot -> first-write
upgrade window), tracked as the follow-up filed alongside this PR; writes
that lose the race surface a raised OperationalError and are tolerated here.
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base
from octave.middleware import add_error_handlers
from octave.routes.auth import router as auth_router
from octave.routes.sessions import router as sessions_router


async def _app(tmp_path: Path) -> tuple[FastAPI, str]:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'conc161.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.state.db_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings(cookie_secure=False)
    add_error_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        r = await client.post(
            "/api/auth/bootstrap",
            json={"username": "alice", "password": "pw", "display_name": "Alice"},
        )
        assert r.status_code == 201
        token = r.cookies.get("octave_session", "")
    return app, token


async def test_concurrent_reads_survive_parallel_commits(tmp_path: Path) -> None:
    app, token = await _app(tmp_path)
    headers = {"cookie": f"octave_session={token}"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t", timeout=30.0
    ) as client:

        async def get_sessions(i: int) -> int:
            r = await client.get("/api/sessions?limit=50&offset=0", headers=headers)
            return r.status_code

        async def create_session(i: int) -> int:
            try:
                r = await client.post(
                    "/api/sessions", json={"title": f"s{i}"}, headers=headers
                )
                return r.status_code
            except OperationalError:
                # Writer-vs-writer lock loss: the out-of-scope residual
                # (follow-up issue). Reads above must never see this.
                return -1

        codes = await asyncio.gather(
            *(get_sessions(i) for i in range(25)),
            *(create_session(i) for i in range(25)),
        )
    reads = codes[:25]
    writes = codes[25:]
    assert set(reads) == {200}, f"expected no 500s, got {sorted(set(reads))}"
    assert 201 in writes, "expected at least one committed write"
    assert set(writes) <= {201, -1}
