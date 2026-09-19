from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from octave.db.lifespan import db_lifespan
from octave.mcp.lifespan import mcp_lifespan
from octave.middleware import LogRequestMiddleware, add_cors, add_error_handlers
from octave.routes.health import router as health_router
from octave.websocket.connection import router as ws_router

AppLifespan = Callable[[FastAPI], AbstractAsyncContextManager[None, bool | None]]
"""A lifespan callable: async context factory taking the app."""


def compose_lifespans(*lifespans: AppLifespan) -> AppLifespan:
    """Compose lifespans: enter in order, exit in reverse (stack semantics)."""

    @asynccontextmanager
    async def _composed(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for lifespan in lifespans:
                await stack.enter_async_context(lifespan(app))
            yield

    return _composed


app = FastAPI(
    title="Octave Backend", lifespan=compose_lifespans(db_lifespan, mcp_lifespan)
)

# Middleware
add_cors(app)
add_error_handlers(app)
app.add_middleware(LogRequestMiddleware)

# Routes
app.include_router(health_router, prefix="/api")
app.include_router(ws_router)
