# Development Guide

This guide covers running the development environment, executing tests, and configuring the project.

## Prerequisites

- **Docker** (29.x+) — for containerized development
- **uv** (Python package manager) — for local backend development
- **Node.js** (20.x+) — for local frontend development
- **Python** (3.12) — managed by `uv`

---

## Running the Development Environment

### Option 1: Docker Compose (Recommended)

Starts both backend and frontend services with health checks and hot-reload for the frontend.

```bash
# Start both services
docker compose up --build

# Run in background
docker compose up --build -d

# Stop services
docker compose down
```

**Services exposed:**
| Service | URL | Port |
|---------|-----|------|
| Backend (FastAPI) | `http://localhost:8000` | 8000 |
| Backend API Docs | `http://localhost:8000/docs` | 8000 |
| Frontend (Vite) | `http://localhost:5173` | 5173 |

**How it works:**
- Backend starts first and passes a health check (`/health` endpoint)
- Frontend starts only after backend is healthy (`depends_on: condition: service_healthy`)
- Frontend source (`./frontend/src`) is mounted as a volume for hot-reload
- Vite proxies `/api/*` requests to the backend

### Option 2: Local Development (Without Docker)

#### Backend

```bash
cd backend

# Install dependencies (including dev)
uv sync --extra dev

# Run the server
uv run uvicorn octave.app:app --reload --host 0.0.0.0 --port 8000
```

#### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

---

## Running Tests

### Backend Tests

```bash
cd backend

# Install dev dependencies first
uv sync --extra dev

# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_health.py -v

# Run with coverage
uv run pytest tests/ -v --cov=octave --cov-report=term-missing
```

**Backend quality checks:**

```bash
# Linting (ruff)
uv run ruff check src/ tests/

# Auto-fix linting issues
uv run ruff check --fix src/ tests/

# Type checking (mypy)
uv run mypy src/
```

### Frontend Tests

```bash
cd frontend

# Install dependencies first
npm install

# Unit tests (Vitest)
npm run test

# Unit tests in watch mode
npm run test:watch

# E2E tests (Playwright)
npm run test:e2e

# Install Playwright browsers (first time)
npx playwright install chromium
```

**Frontend quality checks:**

```bash
# Linting (ESLint)
npm run lint

# Format code (Prettier)
npm run format

# Check formatting without writing
npm run format:check

# TypeScript type checking
npx tsc --noEmit
```

---

## Environment Configuration

### Backend Environment

The backend reads configuration from environment variables. Create `backend/.env` for local development:

```env
# Server
HOST=0.0.0.0
PORT=8000

# Database (future)
# DATABASE_URL=sqlite:///./octave.db
```

> **Note:** `.env` files are in `.gitignore` and never committed.

#### Inference Adapter

The inference layer (`octave.inference`) reads `OCTAVE_INFERENCE_*` variables,
via `InferenceSettings` in `backend/src/octave/inference/config.py`:

| Variable | Default | Description |
|---|---|---|
| `OCTAVE_INFERENCE_ADAPTER` | `openai` | Registered adapter name, or a `module.path:ClassName` import string for a third-party adapter package |
| `OCTAVE_INFERENCE_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible engine endpoint (Ollama, vLLM, llama.cpp server, LM Studio, …) |
| `OCTAVE_INFERENCE_API_KEY` | *(empty)* | Bearer token; optional for local engines. Secret — never logged (redacted `***`) |
| `OCTAVE_INFERENCE_DEFAULT_MODEL` | *(unset)* | Model used when a request doesn't specify one |
| `OCTAVE_INFERENCE_TIMEOUT_SECONDS` | `120.0` | Per-request timeout |
| `OCTAVE_INFERENCE_MAX_RETRIES` | `2` | SDK-level retries on transient failures |

**HTTPS engines behind an internal CA:** if the engine uses a certificate from
a private CA, httpx (certifi bundle) will fail the handshake with a generic
connection error even though `curl` succeeds. Point it at the system trust
store:

```env
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
```

This *adds* to certifi's bundle rather than replacing it, so public HTTPS
endpoints keep working. Verify any setup with `backend/scripts/smoke_inference.py`.

### Frontend Environment

The frontend uses Vite's environment variable convention. Create `frontend/.env` for local development:

```env
# API URL for non-Docker development
VITE_API_URL=http://localhost:8000
```

**Environment variable rules:**
- Only variables prefixed with `VITE_` are exposed to the browser
- Access in code via `import.meta.env.VITE_API_URL`
- In Docker Compose, `VITE_API_URL` is set to `http://localhost:8000`

### Docker Compose Configuration

[`docker-compose.yml`](../docker-compose.yml) defines two services:

**Backend service:**
- Builds from `backend/Dockerfile`
- Exposes port 8000
- Health check polls `/health` every 30s (10s timeout, 3 retries, 5s start period)

**Frontend service:**
- Builds from `frontend/Dockerfile`
- Exposes port 5173
- Depends on backend being healthy
- Mounts `./frontend/src` for hot-reload

---

## Project Structure

```
Octave/
├── docker-compose.yml          # Service orchestration
├── backend/
│   ├── Dockerfile              # Backend container
│   ├── pyproject.toml          # Python project config (uv, pytest, ruff, mypy)
│   ├── .python-version         # Python 3.12
│   ├── uv.lock                 # Resolved dependencies
│   ├── src/octave/
│   │   ├── __init__.py
│   │   └── app.py              # FastAPI application
│   └── tests/
│       └── test_health.py      # Backend tests
└── frontend/
    ├── Dockerfile              # Frontend container
    ├── package.json            # Node project config
    ├── tsconfig.json           # TypeScript config
    ├── vite.config.ts          # Vite build + dev server
    ├── vitest.config.ts        # Vitest unit test config
    ├── playwright.config.ts    # Playwright E2E config
    ├── eslint.config.js        # ESLint config
    ├── .prettierrc             # Prettier config
    ├── src/
    │   ├── main.tsx            # React entry point
    │   ├── App.tsx             # Root component
    │   ├── index.css           # Global styles
    │   ├── vite-env.d.ts       # Vite type declarations
    │   └── components/
    │       └── HealthCheck.tsx # Health check UI
    └── tests/
        ├── App.test.tsx        # Unit tests
        └── e2e/
            └── health.spec.ts  # E2E tests
```

---

## Troubleshooting

### Port already in use

```bash
# Find what's using a port
lsof -i :8000   # Backend
lsof -i :5173   # Frontend

# Kill the process
kill -9 <PID>
```

### Docker build cache issues

```bash
# Clean rebuild
docker compose build --no-cache
```

### Frontend hot-reload not working in Docker

Ensure `./frontend/src:/app/src` volume mount is present in `docker-compose.yml`. The Vite dev server watches this directory.

### Backend health check failing in Docker Compose

The health check uses Python's `urllib` (available in standard library). Verify the `/health` endpoint responds:

```bash
docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())"
```
