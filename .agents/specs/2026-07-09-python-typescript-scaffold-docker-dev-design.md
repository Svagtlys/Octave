# Python/TypeScript Scaffold + Docker Dev Environment — Design

**Date:** 2026-07-09
**Branch:** `feature/python-typescript-scaffold-docker-dev`
**PR:** https://github.com/Svagtlys/Octave/pull/66

## Goal

Set up the foundational project scaffold including Python and TypeScript source directories, configuration files, and a Docker Compose development environment — providing a green baseline where both services build, run, and pass tests.

## Architecture

Monorepo with two independent services communicating over localhost via Docker Compose networking.

```
Octave/
├── docker-compose.yml
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── .python-version
│   ├── uv.lock
│   ├── src/
│   │   └── octave/
│   │       ├── __init__.py
│   │       └── app.py
│   └── tests/
│       ├── __init__.py
│       └── test_health.py
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── vitest.config.ts
│   ├── playwright.config.ts
│   ├── eslint.config.js
│   ├── .prettierrc
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   └── components/
│   │       └── HealthCheck.tsx
│   └── tests/
│       ├── App.test.tsx
│       └── e2e/
│           └── health.spec.ts
└── .gitignore
```

## Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Directory layout | Monorepo with `backend/` + `frontend/` | Clean separation, matches existing `.gitignore` entries, maps to Docker services |
| Python version | 3.12 | Current LTS, stable ecosystem |
| Node version | 22 | Current LTS |
| Python packaging | `uv` | Lock files for reproducible Docker builds, fast resolution |
| Python testing | `pytest` | Standard Python test framework |
| Python linting | `ruff` | Fast, comprehensive, already referenced in `.gitignore` |
| Python type checking | `mypy` | Enforces type hint requirement from coding rules |
| Frontend framework | React + Vite | Specified in architecture docs |
| Frontend unit testing | Vitest | Fast, Vite-native, JSDOM support for React |
| Frontend E2E testing | Playwright | Specified in coding rules, real browser testing |
| Frontend linting | ESLint + Prettier | TypeScript + React hooks + formatting |

## Components

### Backend (`backend/`)

**Dockerfile:**
- Base image: `python:3.12-slim`
- Install `uv` via official installer
- Copy `pyproject.toml` and `uv.lock` for dependency layer caching
- Run `uv sync --frozen` for reproducible install
- Copy source code
- Expose port 8000
- Run `uvicorn octave.app:app --host 0.0.0.0 --port 8000`

**pyproject.toml:**
- Project name: `octave-backend`
- Dependencies: `fastapi`, `uvicorn[standard]`
- Dev dependencies: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`
- Ruff configuration inline (line length 88, target Python 3.12)
- Mypy configuration inline (strict mode, warn_return_any, etc.)

**.python-version:**
- Contains `3.12` for tooling recognition

**FastAPI Application (`src/octave/app.py`):**
- Single `/health` endpoint returning `{"status": "ok"}`
- Returns 200 status code
- Used by Docker health check and frontend health component

**Tests (`tests/test_health.py`):**
- Uses `httpx.AsyncClient` against TestClient
- Verifies `/health` returns 200 with `{"status": "ok"}`

### Frontend (`frontend/`)

**Dockerfile:**
- Base image: `node:22-alpine`
- Copy `package.json` and `package-lock.json` for dependency layer caching
- Run `npm ci` for reproducible install
- Copy source code
- Expose port 5173
- Run `npm run dev -- --host 0.0.0.0`

**package.json:**
- Dependencies: `react`, `react-dom`
- Dev dependencies: `vite`, `@vitejs/plugin-react`, `typescript`, `@types/react`, `@types/react-dom`, `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`, `playwright`, `eslint`, `@typescript-eslint/eslint-plugin`, `@typescript-eslint/parser`, `eslint-plugin-react-hooks`, `prettier`, `eslint-config-prettier`, `eslint-plugin-prettier`
- Scripts: `dev`, `build`, `preview`, `test`, `test:e2e`, `lint`, `format`, `format:check`

**tsconfig.json:**
- Strict mode enabled
- ESNext target
- DOM + ESNext lib
- Module resolution bundler (for Vite compatibility)
- Path alias `@/*` mapping to `src/*`

**vite.config.ts:**
- React plugin
- Dev server on port 5173
- Proxy `/api` to `http://backend:8000` (Docker service name)
- Path alias `@/*` to `./src/*`

**vitest.config.ts:**
- JSDOM environment
- Includes `src/**/*.test.tsx` and `src/**/*.test.ts`

**playwright.config.ts:**
- Chromium browser
- Test directory: `tests/e2e/`
- Web server: starts Vite dev server for E2E context

**eslint.config.js:**
- Flat config format
- TypeScript + React hooks rules
- Prettier integration

**.prettierrc:**
- Single quotes, semicolons, 2-space indent, print width 100

**Application (`src/App.tsx`):**
- Minimal React component rendering "Octave" heading
- Imports `HealthCheck` component

**HealthCheck Component (`src/components/HealthCheck.tsx`):**
- Fetches `/api/health` on mount
- Displays loading, success, and error states
- Uses inline styles (per coding rules)

**Unit Test (`tests/App.test.tsx`):**
- Renders App component
- Verifies "Octave" text is present

**E2E Test (`tests/e2e/health.spec.ts`):**
- Navigates to app
- Verifies health status displays from backend

### Docker Compose

**docker-compose.yml:**
- `backend` service:
  - Build context: `./backend`
  - Port: `8000:8000`
  - Health check: `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"` with 30s interval, 10s timeout, 5s start period, 3 retries
- `frontend` service:
  - Build context: `./frontend`
  - Port: `5173:5173`
  - Depends on `backend` with `service_healthy` condition
  - Environment: `VITE_API_URL=http://localhost:8000` for host-side proxy

### .gitignore Updates

Add TypeScript/frontend artifacts (most already present, confirming completeness):
```
# TypeScript build artifacts
frontend/dist/
frontend/.vite/
frontend/playwright-report/
frontend/test-results/
*.tsbuildinfo

# Node modules (already present)
frontend/node_modules/
node_modules/

## Files to commit (do NOT ignore):
- `uv.lock` — committed for reproducible backend builds
- `frontend/package-lock.json` — committed for reproducible frontend builds

# Already present and confirmed
backend/__pycache__/
backend/.venv/
backend/.pytest_cache/
backend/.ruff_cache/
backend/mypy_cache/
```

**Note:** `uv.lock` and `package-lock.json` are committed for reproducible builds. Only `.venv/` and `node_modules/` are ignored.

## Data Flow

Minimal interaction at this stage:
1. Docker Compose starts `backend` (port 8000) and waits for health check
2. Once healthy, starts `frontend` (port 5173)
3. Frontend Vite proxy forwards `/api/*` to backend
4. `HealthCheck.tsx` fetches `/api/health`, displays result

## Testing Strategy

| Test | Framework | Command | Verifies |
|------|-----------|---------|----------|
| `test_health.py` | pytest | `cd backend && uv run pytest -v` | `/health` returns 200 with `{"status": "ok"}` |
| `App.test.tsx` | Vitest | `cd frontend && npm run test` | App renders "Octave" heading |
| `health.spec.ts` | Playwright | `cd frontend && npm run test:e2e` | App loads, health status visible |

## Success Criteria

- `docker compose up --build` starts both services without errors
- `curl http://localhost:8000/health` returns `{"status":"ok"}`
- Frontend loads at `http://localhost:5173` and displays health status
- Backend tests pass: `cd backend && uv run pytest -v`
- Frontend unit tests pass: `cd frontend && npm run test`
- Frontend E2E tests pass: `cd frontend && npm run test:e2e`
- Linting passes: `cd backend && uv run ruff check` and `cd frontend && npm run lint`
- Type checking passes: `cd backend && uv run mypy src/` and `cd frontend && npx tsc --noEmit`
