"""reset-password CLI: reads password from file/stdin (never argv)."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.auth.cli import main
from octave.auth.store import AuthStore
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base, User


def _reset(db_url: str, username: str, password_file: Path) -> int:
    return main(
        [
            "reset-password",
            "--db-url",
            db_url,
            username,
            "--password-file",
            str(password_file),
        ]
    )


def _seed_db(tmp_path: Path) -> str:
    """Standalone DB file (the CLI opens its own connection, so the test
    builds the schema + a user through production's engine choke point)."""
    db_path = tmp_path / "cli.db"
    url = f"sqlite:///{db_path}"

    async def _build() -> None:
        engine = create_sqlite_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await AuthStore(session).create_user(
                username="alice", display_name="Alice"
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_build())
    return url


def _stored_hash(db_url: str) -> str | None:
    async def _read() -> str | None:
        engine = create_sqlite_engine(db_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                user = (
                    await s.execute(select(User).where(User.username == "alice"))
                ).scalar_one()
                return user.password_hash
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_reset_password_from_file_sets_hash(tmp_path: Path) -> None:
    db_url = _seed_db(tmp_path)
    pw_file = tmp_path / "pw.txt"
    pw_file.write_text("  new-secret\n")  # surrounding whitespace stripped
    assert _reset(db_url, "alice", pw_file) == 0
    # argon2-encoded (real hasher here — one hash in the suite is fine)
    encoded = _stored_hash(db_url)
    assert encoded is not None and encoded.startswith("$argon2")


def test_reset_password_unknown_user_exits_nonzero(tmp_path: Path) -> None:
    db_url = _seed_db(tmp_path)
    pw_file = tmp_path / "pw.txt"
    pw_file.write_text("secret")
    assert _reset(db_url, "nobody", pw_file) != 0


def test_password_never_taken_from_argv() -> None:
    """Positional password args are rejected — secrets must not land in
    shell history / ps output."""
    with pytest.raises(SystemExit):
        main(["reset-password", "--db-url", "x", "alice", "hunter2"])
