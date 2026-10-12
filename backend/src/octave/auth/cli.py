"""``octave-auth reset-password`` — break-glass credential recovery.

Prompt-less by design: the password arrives via ``--password-file`` or
stdin, never argv (argv lands in shell history and ``ps`` output). Runs a
sync one-shot against the configured DB — the only auth code path outside
the app.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.auth.passwords import Argon2Hasher
from octave.auth.store import AuthStore
from octave.db._bootstrap import create_sqlite_engine

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="octave-auth",
        description="Octave account maintenance.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reset = sub.add_parser(
        "reset-password",
        help="Set a user's password (password via --password-file or stdin).",
    )
    reset.add_argument("--db-url", required=True, help="sqlite database URL/path")
    reset.add_argument("username")
    reset.add_argument(
        "--password-file",
        type=Path,
        default=None,
        help="File containing the new password. Omit to read stdin.",
    )
    return parser


async def _run_reset(args: argparse.Namespace) -> int:
    if args.password_file is not None:
        raw = args.password_file.read_text()
    else:
        raw = sys.stdin.read()
    password = raw.strip()
    if not password:
        print("error: empty password", file=sys.stderr)
        return 2

    engine = create_sqlite_engine(args.db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            store = AuthStore(session)
            user = await store.get_by_username(args.username)
            if user is None:
                print(
                    f"error: no user {args.username!r}",
                    file=sys.stderr,
                )
                return 1
            await store.set_password_hash(user, Argon2Hasher().hash(password))
            await session.commit()
    finally:
        await engine.dispose()
    print(f"password reset for {user.username}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """argparse dispatch. Unknown user exits 1; misuse exits 2 (argparse)."""
    args = _build_parser().parse_args(argv)
    if args.command == "reset-password":
        return asyncio.run(_run_reset(args))
    raise AssertionError(f"unhandled command {args.command!r}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
