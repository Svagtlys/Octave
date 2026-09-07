# Backend-Frontend Communication Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Wire the REST API and WebSocket channel between the Octave React frontend and FastAPI backend, including non-root Docker configuration.

**Architecture:** Modular FastAPI backend (middleware, routes, websocket as separate modules). Frontend fetch wrapper and WebSocket hook in `src/lib/api/`. Both Docker containers run as non-root users.

**Tech Stack:** FastAPI + uvicorn (backend), React 19 + Vite + TypeScript (frontend), pytest + httpx (backend tests), Vitest + React Testing Library (frontend tests).

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Create | `backend/src/octave/middleware.py` | CORS, request logging, error handlers |
| Create | `backend/src/octave/routes/__init__.py` | Routes package |
| Create | `backend/src/octave/routes/health.py` | Health + version endpoints |
| Create | `backend/src/octave/websocket/__init__.py` | WebSocket package |
| Create | `backend/src/octave/websocket/connection.py` | WebSocket echo endpoint |
| Modify | `backend/src/octave/app.py` | App factory wiring middleware + routes + WS |
| Modify | `backend/tests/test_health.py` | Update path from `/health` to `/api/health` |
| Create | `backend/tests/test_version.py` | Version endpoint test |
| Create | `backend/tests/test_cors.py` | CORS headers test |
| Create | `backend/tests/test_error_handling.py` | JSON error format test |
| Create | `backend/tests/test_websocket.py` | WebSocket echo test |
| Create | `frontend/src/lib/api/client.ts` | Fetch wrapper + ApiError |
| Create | `frontend/src/lib/api/useWebSocket.ts` | WebSocket React hook |
| Create | `frontend/src/lib/api/client.test.ts` | API client tests |
| Create | `frontend/src/lib/api/useWebSocket.test.ts` | WebSocket hook tests |
| Create | `frontend/src/lib/index.ts` | Barrel export for lib |
| Modify | `backend/Dockerfile` | Add non-root user |
| Modify | `frontend/Dockerfile` | Add non-root user |

---

## Task 1: Backend — Middleware Module

**Files:**
- Create: `backend/src/octave/middleware.py`

- [x] **Step 1: Create `middleware.py` with CORS, logging, and error handlers**

```python
import logging
import time
import os
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp
from fastapi import FastAPI

logger = logging.getLogger(__name__)


def add_cors(app: FastAPI) -> None:
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


async def log_request_middleware(request: Request, call_next: ASGIApp) -> JSONResponse:
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    logger.info(
        "%s %s %s %.3fs",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )
    return response


def add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": "Not Found", "detail": str(exc)},
        )

    @app.exception_handler(500)
    async def server_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error", "detail": str(exc)},
        )
```

- [x] **Step 2: Commit**

```bash
git add backend/src/octave/middleware.py
git commit -m "feat: add middleware module with CORS, logging, error handlers"
```

---

## Task 2: Backend — Health Routes Module

**Files:**
- Create: `backend/src/octave/routes/__init__.py`
- Create: `backend/src/octave/routes/health.py`

- [x] **Step 1: Create empty `routes/__init__.py`**

```python
```

- [x] **Step 2: Create `routes/health.py` with health and version endpoints**

> **Note:** Route paths are relative (`/health`, `/version`). The `/api` prefix is applied when the router is included in `app.py` via `app.include_router(health_router, prefix="/api")`, producing final URLs `/api/health` and `/api/version`.

```python
import os
from importlib import metadata
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version")
async def version() -> dict[str, str]:
    try:
        v = metadata.version("octave-backend")
    except metadata.PackageNotFoundError:
        v = "unknown"
    env = os.getenv("OCTAVE_ENV", "development")
    return {"version": v, "env": env}
```

- [x] **Step 3: Commit**

```bash
git add backend/src/octave/routes/
git commit -m "feat: add health and version route module"
```

---

## Task 3: Backend — WebSocket Module

**Files:**
- Create: `backend/src/octave/websocket/__init__.py`
- Create: `backend/src/octave/websocket/connection.py`

- [x] **Step 1: Create empty `websocket/__init__.py`**

```python
```

- [x] **Step 2: Create `websocket/connection.py` with echo WebSocket endpoint**

```python
import json
from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            response = {
                "type": "pong",
                "payload": message.get("payload", ""),
            }
            await websocket.send_text(json.dumps(response))
    except Exception:
        await websocket.close()
```

- [x] **Step 3: Commit**

```bash
git add backend/src/octave/websocket/
git commit -m "feat: add WebSocket echo endpoint module"
```

---

## Task 4: Backend — Wire App Factory

**Files:**
- Modify: `backend/src/octave/app.py`

- [x] **Step 1: Rewrite `app.py` to wire middleware, routes, and WebSocket**

> **Note:** `prefix="/api"` on `include_router` prepends `/api` to all route paths defined in `health.py`, so `/health` becomes `/api/health`.

```python
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
```

- [x] **Step 2: Commit**

```bash
git add backend/src/octave/app.py
git commit -m "feat: wire middleware, routes, and WebSocket in app factory"
```

---

## Task 5: Backend — Update Health Test

**Files:**
- Modify: `backend/tests/test_health.py`

- [x] **Step 1: Update existing health test to use `/api/health` path**

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [x] **Step 2: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add backend/tests/test_health.py
git commit -m "fix: update health test to use /api/health path"
```

---

## Task 6: Backend — Version Endpoint Test

**Files:**
- Create: `backend/tests/test_version.py`

- [x] **Step 1: Write the failing test**

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_version_endpoint_returns_version() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/version")

    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "env" in data
    assert isinstance(data["version"], str)
    assert isinstance(data["env"], str)
```

- [x] **Step 2: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_version.py -v`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add backend/tests/test_version.py
git commit -m "test: add version endpoint test"
```

---

## Task 7: Backend — CORS Test

**Files:**
- Create: `backend/tests/test_cors.py`

- [x] **Step 1: Write the CORS headers test**

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_cors_headers_present() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/health",
            headers={"Origin": "http://localhost:5173"},
        )

    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
```

- [x] **Step 2: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_cors.py -v`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add backend/tests/test_cors.py
git commit -m "test: add CORS headers test"
```

---

## Task 8: Backend — Error Handling Test

**Files:**
- Create: `backend/tests/test_error_handling.py`

- [x] **Step 1: Write the error handling tests**

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_404_returns_json_error() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/nonexistent")

    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert "detail" in data
```

- [x] **Step 2: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_error_handling.py -v`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add backend/tests/test_error_handling.py
git commit -m "test: add error handling JSON format test"
```

---

## Task 9: Backend — WebSocket Test

**Files:**
- Create: `backend/tests/test_websocket.py`

- [x] **Step 1: Write the WebSocket echo test**

```python
import json
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_websocket_echo() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.websocket("/ws") as ws:
            await ws.send_text(json.dumps({"type": "ping", "payload": "hello"}))
            data = await ws.receive_text()
            response = json.loads(data)

    assert response["type"] == "pong"
    assert response["payload"] == "hello"
```

- [x] **Step 2: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_websocket.py -v`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add backend/tests/test_websocket.py
git commit -m "test: add WebSocket echo test"
```

---

## Task 10: Backend — Run Full Test Suite

**Files:** (none — verification step)

- [x] **Step 1: Run all backend tests**

Run: `cd backend && uv run pytest -v`
Expected: All tests PASS

- [x] **Step 2: Run linter**

Run: `cd backend && uv run ruff check src/ tests/`
Expected: No lint errors

- [x] **Step 3: Run type checker**

Run: `cd backend && uv run mypy src/octave/`
Expected: No type errors

---

## Task 11: Frontend — API Client

**Files:**
- Create: `frontend/src/lib/api/client.ts`

- [x] **Step 1: Create the API client module**

```typescript
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new ApiError(response.status, errorBody || response.statusText);
  }

  const text = await response.text();
  if (!text) return {} as T;
  return JSON.parse(text) as T;
}

export async function get<T>(path: string): Promise<T> {
  return request<T>("GET", path);
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("POST", path, body);
}

export async function put<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("PUT", path, body);
}

export async function deleteRequest<T>(path: string): Promise<T> {
  return request<T>("DELETE", path);
}

export interface HealthResponse {
  status: string;
}

export interface VersionResponse {
  version: string;
  env: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return get<HealthResponse>("/api/health");
}

export async function fetchVersion(): Promise<VersionResponse> {
  return get<VersionResponse>("/api/version");
}
```

- [x] **Step 2: Commit**

```bash
git add frontend/src/lib/api/client.ts
git commit -m "feat: add API client with fetch wrapper and typed functions"
```

---

## Task 12: Frontend — API Client Tests

**Files:**
- Create: `frontend/src/lib/api/client.test.ts`

- [x] **Step 1: Write the API client tests**

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { get, ApiError } from "./client";

// Mock global fetch
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe("API client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("parses JSON on success", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: async () => JSON.stringify({ status: "ok" }),
    });

    const result = await get<{ status: string }>("/api/health");
    expect(result).toEqual({ status: "ok" });
  });

  it("throws ApiError on 404", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
      text: async () => "",
    });

    await expect(get("/api/nonexistent")).rejects.toThrow(ApiError);
    try {
      await get("/api/nonexistent");
    } catch (e) {
      expect((e as ApiError).status).toBe(404);
    }
  });

  it("throws ApiError on 500", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      text: async () => "",
    });

    await expect(get("/api/boom")).rejects.toThrow(ApiError);
    try {
      await get("/api/boom");
    } catch (e) {
      expect((e as ApiError).status).toBe(500);
    }
  });

  it("uses BASE_URL from env", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: async () => "",
    });

    await get("/api/health");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/health",
      expect.objectContaining({ method: "GET" }),
    );
  });
});
```

- [x] **Step 2: Run tests to verify they pass**

Run: `cd frontend && npm run test -- src/lib/api/client.test.ts`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add frontend/src/lib/api/client.test.ts
git commit -m "test: add API client unit tests"
```

---

## Task 13: Frontend — WebSocket Hook

**Files:**
- Create: `frontend/src/lib/api/useWebSocket.ts`

- [x] **Step 1: Create the WebSocket hook**

```typescript
import { useState, useRef, useCallback, useEffect } from "react";

export type WebSocketStatus = "connecting" | "open" | "closed" | "error";

interface UseWebSocketReturn {
  status: WebSocketStatus;
  send: (message: string) => void;
  lastMessage: string | null;
}

function getWsUrl(url?: string): string {
  if (url) return url;
  const apiUrl = import.meta.env.VITE_API_URL || "http://localhost:8000";
  const wsProtocol = apiUrl.startsWith("https") ? "wss" : "ws";
  const host = apiUrl.replace(/^https?:\/\//, "");
  return `${wsProtocol}://${host}/ws`;
}

export function useWebSocket(url?: string): UseWebSocketReturn {
  const [status, setStatus] = useState<WebSocketStatus>("connecting");
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef<number>(1000);

  const connect = useCallback(() => {
    const wsUrl = getWsUrl(url);
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setStatus("open");
      backoffRef.current = 1000;
    };

    ws.onmessage = (event) => {
      setLastMessage(event.data);
    };

    ws.onerror = () => {
      setStatus("error");
    };

    ws.onclose = () => {
      setStatus("closed");
      // Reconnect with exponential backoff
      const delay = Math.min(backoffRef.current, 8000);
      backoffRef.current *= 2;
      reconnectTimeoutRef.current = setTimeout(connect, delay);
    };

    wsRef.current = ws;
  }, [url]);

  const send = useCallback(
    (message: string) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(message);
      }
    },
    [],
  );

  useEffect(() => {
    setStatus("connecting");
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return { status, send, lastMessage };
}
```

- [x] **Step 2: Commit**

```bash
git add frontend/src/lib/api/useWebSocket.ts
git commit -m "feat: add WebSocket hook with reconnect and exponential backoff"
```

---

## Task 14: Frontend — WebSocket Hook Tests

**Files:**
- Create: `frontend/src/lib/api/useWebSocket.test.ts`

- [x] **Step 1: Write the WebSocket hook tests**

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";

// Mock WebSocket
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  url: string;
  readyState: number = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  send(_data: string) {}

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}

vi.stubGlobal("WebSocket", MockWebSocket);

describe("useWebSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns initial connecting status", () => {
    const { result } = renderHook(() =>
      // @ts-expect-error — MockWebSocket is not a real WebSocket
      require("./useWebSocket").useWebSocket("ws://localhost:8000/ws"),
    );
    expect(result.current.status).toBe("connecting");
  });

  it("provides send function", () => {
    const { result } = renderHook(() =>
      // @ts-expect-error — MockWebSocket is not a real WebSocket
      require("./useWebSocket").useWebSocket("ws://localhost:8000/ws"),
    );
    expect(typeof result.current.send).toBe("function");
  });

  it("lastMessage is initially null", () => {
    const { result } = renderHook(() =>
      // @ts-expect-error — MockWebSocket is not a real WebSocket
      require("./useWebSocket").useWebSocket("ws://localhost:8000/ws"),
    );
    expect(result.current.lastMessage).toBeNull();
  });
});
```

- [x] **Step 2: Run tests to verify they pass**

Run: `cd frontend && npm run test -- src/lib/api/useWebSocket.test.ts`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add frontend/src/lib/api/useWebSocket.test.ts
git commit -m "test: add WebSocket hook unit tests"
```

---

## Task 15: Frontend — Run Full Test Suite

**Files:** (none — verification step)

- [x] **Step 1: Run all frontend tests**

Run: `cd frontend && npm run test`
Expected: All tests PASS

- [x] **Step 2: Run linter**

Run: `cd frontend && npm run lint`
Expected: No lint errors

- [x] **Step 3: Run TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

---

## Task 16: Backend Dockerfile — Non-Root User

**Files:**
- Modify: `backend/Dockerfile`

- [x] **Step 1: Add non-root user to backend Dockerfile**

Add before the `CMD` line:
```dockerfile
# Create non-root user
RUN useradd --create-home octave
USER octave
```

Final Dockerfile should look like:
```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-install-project

# Copy source code
COPY src/ src/

# Create non-root user
RUN useradd --create-home octave
USER octave

# Expose port
EXPOSE 8000

# Run the application
CMD ["uv", "run", "uvicorn", "octave.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [x] **Step 2: Commit**

```bash
git add backend/Dockerfile
git commit -m "chore: run backend container as non-root user"
```

---

## Task 17: Frontend Dockerfile — Non-Root User

**Files:**
- Modify: `frontend/Dockerfile`

- [x] **Step 1: Add non-root user to frontend Dockerfile**

Add before the `CMD` line:
```dockerfile
# Use built-in non-root user
USER node
```

Final Dockerfile should look like:
```dockerfile
FROM node:22-alpine

WORKDIR /app

# Copy dependency files for layer caching
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Use built-in non-root user
USER node

# Expose port
EXPOSE 5173

# Run Vite dev server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

- [x] **Step 2: Commit**

```bash
git add frontend/Dockerfile
git commit -m "chore: run frontend container as non-root user"
```

---

## Task 18: Final Verification

**Files:** (none — verification step)

- [x] **Step 1: Run all backend tests**

Run: `cd backend && uv run pytest -v`
Expected: All tests PASS

- [x] **Step 2: Run all frontend tests**

Run: `cd frontend && npm run test`
Expected: All tests PASS

- [x] **Step 3: Verify Docker build**

Run: `docker compose build`
Expected: Both images build successfully

- [x] **Step 4: Verify non-root user in containers**

Run: `docker compose up -d && docker exec $(docker compose ps -q backend) whoami && docker exec $(docker compose ps -q frontend) whoami`
Expected: `octave` for backend, `node` for frontend

- [x] **Step 5: Stop containers**

Run: `docker compose down`

---

## Self-Review Checklist

- [x] **Spec coverage:** All design doc sections covered — middleware (Task 1), routes (Task 2), WebSocket (Task 3), app wiring (Task 4), backend tests (Tasks 5-10), frontend client (Tasks 11-12), WebSocket hook (Tasks 13-14), Docker non-root (Tasks 16-17), final verification (Task 18).
- [x] **Placeholder scan:** No TBD, TODO, or vague instructions. All code blocks are complete.
- [x] **Type consistency:** `ApiError` class used consistently across client.ts and client.test.ts. WebSocket message types (`ping`/`pong`) match between backend connection.py and test_websocket.py. Health endpoint path `/api/health` consistent across all references.
- [x] **DRY:** No duplicated code between tasks. Each file created once.
- [x] **YAGNI:** No extra features beyond the spec. No auth, no agent routing, no SSE.
