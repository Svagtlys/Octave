#!/usr/bin/env python
"""Manual smoke test: the full request flow over WebSocket (issue #60).

User message -> context injection -> inference -> MCP tool call -> reply,
driven through the real WS endpoint with the real seams wired directly
(like smoke_agent_loop.py): temp SQLite, stdio filesystem server, live
OpenAI-dialect engine from OCTAVE_INFERENCE_* env.

The MCP fleet and DB live inside the app lifespan so every anyio scope
(fleet task group, MCP client streams) stays on TestClient's portal loop
— the same loop the WS handler runs tool calls on. Splitting setup and
teardown across separate ``asyncio.run`` calls would crash with
"Attempted to exit cancel scope in a different task".

Usage (from backend/):

    uv run scripts/smoke_e2e_flow.py
    uv run scripts/smoke_e2e_flow.py --base-url http://localhost:11434/v1 \
        --model qwen2.5-coder

Requires Node/npx. Prints the transcript; exits non-zero when a stage is
missing or the file was not created. Not part of the test suite.
"""

import argparse
import json
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.testclient import TestClient

from octave.agent import AgentInstanceManager
from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.auth.service import AuthService
from octave.auth.store import AuthStore
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import (
    Agent,
    Base,
    Participant,
    Session,
    SessionParticipant,
    User,
)
from octave.inference import AdapterConfig, InferenceSettings, default_registry
from octave.mcp import McpServerManager, StdioConfig, ToolRegistry
from octave.websocket.connection import router as ws_router

DEFAULT_PROMPT = (
    "Create a file named note.txt in the working directory containing "
    "exactly the text: hello from octave"
)
EXPECTED_CHAIN = [
    "user_message",
    "context_injection",
    "tool_call",
    "tool_result",
    "assistant_message",
]


async def seed(factory: async_sessionmaker[AsyncSession], model: str) -> str:
    """User + agent + session (+ alice's membership) + instance; returns
    the auth token. Alice is the session owner AND a member (the router's
    _require_membership gate)."""
    async with factory() as s:
        store = AuthStore(s)
        await store.create_user(
            username="alice", display_name="Alice", password_hash="dummy$pw"
        )
        s.add(
            Agent(
                id="a_oct",
                name="Octave",
                model_binding={
                    "kind": "explicit",
                    "adapter": "openai",
                    "model": model,
                },
            )
        )
        alice_id = await s.scalar(select(User.id).where(User.username == "alice"))
        alice_pid = await s.scalar(
            select(Participant.id).where(Participant.user_id == alice_id)
        )
        s.add(Session(id="s_smoke", created_by_user_id=alice_id, status="active"))
        s.add(SessionParticipant(session_id="s_smoke", participant_id=alice_pid))
        await s.commit()
        service = AuthService(
            store=store, hasher=DummyHasher(), settings=AuthSettings()
        )
        token = await service.login("alice", "pw")
        await s.commit()
    async with factory() as s:
        await AgentInstanceManager(s).spawn(agent_id="a_oct", session_id="s_smoke")
        await s.commit()
    return token


def build_app(directory: Path, args) -> FastAPI:
    """App with every seam resolved inside its lifespan (portal-loop-safe)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_sqlite_engine(f"sqlite:///{directory / 'smoke.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )

        config = InferenceSettings().to_adapter_config()
        if args.base_url or args.model:
            config = AdapterConfig(
                adapter=config.adapter,
                base_url=args.base_url or config.base_url,
                api_key=config.api_key,
                default_model=args.model or config.default_model,
                timeout_seconds=config.timeout_seconds,
                max_retries=config.max_retries,
                extra=config.extra,
            )
        token = await seed(factory, config.default_model or "unknown-model")

        manager = McpServerManager()
        manager.register(
            id="fs",
            name="filesystem",
            config=StdioConfig(
                command="npx",
                args=[
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    str(directory),
                ],
            ),
        )
        registry = ToolRegistry(manager=manager)
        await manager.start_all()
        state = "stopped"
        for _ in range(30):
            state = manager.status_of("fs").state
            if state in ("connected", "crashed"):
                break
            await anyio.sleep(2.0)
        if state != "connected":
            raise SystemExit(f"filesystem server state={state}")
        await registry.start()
        await registry.refresh("fs")

        adapter = default_registry.create(config)

        app.state.db_session_factory = factory
        app.state.auth_hasher = DummyHasher()
        app.state.auth_settings = AuthSettings()
        app.state.inference_adapter = adapter
        app.state.inference_adapter_name = config.adapter
        app.state.mcp_registry = registry
        app.state.smoke_token = token
        try:
            yield
        finally:
            await registry.stop()
            await manager.stop_all()
            await adapter.aclose()
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_router)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    directory = Path(tempfile.mkdtemp(prefix="octave-smoke-e2e-"))
    print(f"sandbox:  {directory}")
    app = build_app(directory, args)

    got_kinds: list[str] = []
    with TestClient(app) as client:
        token = client.app.state.smoke_token
        with client.websocket_connect(
            "/ws", headers={"cookie": f"octave_session={token}"}
        ) as ws:
            ws.send_text(json.dumps({"type": "join", "session_id": "s_smoke"}))
            print(f"ws:       {json.loads(ws.receive_text())['type']}")
            ws.send_text(
                json.dumps(
                    {
                        "type": "user_message",
                        "session_id": "s_smoke",
                        "content": args.prompt,
                    }
                )
            )
            while True:
                frame = json.loads(ws.receive_text())
                if frame["type"] == "error":
                    print(f"✗ error frame: {frame['error']}", file=sys.stderr)
                    sys.exit(1)
                if frame["type"] != "event":
                    continue
                got_kinds.append(frame["kind"])
                print(
                    f"event:    seq={frame['seq']} kind={frame['kind']} "
                    f"author={frame['author']} "
                    f"content={str(frame['content'])[:100]}"
                )
                if frame["kind"] == "assistant_message":
                    break

    created = directory / "note.txt"
    ok = True
    if got_kinds != EXPECTED_CHAIN:
        print(f"✗ transcript chain {got_kinds} != {EXPECTED_CHAIN}", file=sys.stderr)
        ok = False
    if not created.exists():
        print(f"✗ {created} was not created", file=sys.stderr)
        ok = False
    if ok:
        print(f"\n✓ full flow verified; {created} created")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
