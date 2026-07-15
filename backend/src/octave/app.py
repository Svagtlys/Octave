from fastapi import FastAPI

from octave.middleware import add_cors, add_error_handlers, log_request_middleware
from octave.routes.health import router as health_router
from octave.websocket.connection import router as ws_router

app = FastAPI(title="Octave Backend")

# Middleware
add_cors(app)
add_error_handlers(app)
app.add_middleware(log_request_middleware)

# Routes
app.include_router(health_router, prefix="/api")
app.include_router(ws_router)
