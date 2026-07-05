# Coding Rules

## General Standards

- Write clear, self-documenting code with explicit type annotations
- Follow existing project conventions (naming, structure, patterns)
- Prefer composition over inheritance
- Keep functions small and focused on a single responsibility
- Error handling must be explicit — no silent failures or bare `except` blocks

## Python Backend

- Use type hints on all function signatures and class attributes
- Follow PEP 8 with 4-space indentation
- Use `async/await` for I/O-bound operations
- All database queries must use parameterized statements (no string interpolation)
- Use `logging` module instead of `print()` for any output
- Tests must cover all new endpoints and services

## Logging

All log messages must follow a pipe-delimited format for structured parsing:

```
{level} | {correlation_id} | {module} | {message}
```

- **Level**: The log level, right-justified to 5 characters for aligned output. Valid levels (in order of severity):
  - ` DEBUG` — Detailed diagnostic information for development and debugging. Disabled in production.
  - ` INFO` — Confirmation that operations are proceeding as expected. Use for notable state changes, startup/shutdown, and successful completions.
  - ` WARN` — Unexpected but handled conditions that may indicate future problems. Use for deprecated API usage, retries, or falling back to defaults.
  - ` ERROR` — Operations that failed but did not crash the process. Use for unhandled exceptions in request handlers, failed MCP calls, or database errors.
  - `FATAL` — Fatal errors that threaten process continuation. Use for unrecoverable state corruption, missing required dependencies, or out-of-memory conditions.
- **Correlation ID**: Unique request/session identifier propagated through the call chain. Generate at entry points (HTTP request, MCP tool call, scheduled job) and pass through all downstream calls.
- **Module**: Source file or module name (e.g. `mcp.gateway`, `context.assembler`).
- **Message**: Clear, actionable description. Include variable values relevant to debugging.

**Python implementation:**

```python
import logging
import uuid
from contextvars import ContextVar

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default=str(uuid.uuid4())[:8])

class CorrelationFilter(logging.Filter):
    def filter(self, record):
        record.correlation_id = correlation_id_var.get()
        # Right-justify level to 5 characters for aligned output
        record.levelname = record.levelname.rjust(5)
        return True

logging.basicConfig(
    format="%(levelname)s | %(correlation_id)s | %(module)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logger.addFilter(CorrelationFilter())
```

**Rules:**
- Every log line must include a correlation ID
- Generate new correlation ID at each top-level entry point (request, job, tool call)
- Use `logger.info()` for operational events, `logger.debug()` for tracing, `logger.error()` for failures
- Never log sensitive data (passwords, API keys, tokens) — redact with `***`
- Include exception context with `logger.exception()` on error paths

## Frontend (React/TypeScript)

- Use functional components with hooks
- Strict TypeScript — no `any` types
- Inline styles only (no CSS frameworks or preprocessors)
- Use TanStack Query for server state management
- All API calls must go through `apiFetch()` client utility
- Components must handle loading, error, and empty states

## Testing

- Integration tests required for all new endpoints
- E2E tests using Playwright for frontend flows
- Unit tests for business logic and utilities
- Tests must run independently (no shared state between tests)

## MCP Integration

- MCP connections must be pluggable via configuration (no hard-coded servers)
- Tool names and descriptions may be overridden per-integration
- Gateway skills must be provided for each MCP integration
- Role tagging (`kb_read`, `kb_write`, etc.) required for all MCP tools

## Documentation

- Docstrings required for all public functions and classes
- Update `docs/API.md` for endpoint changes
- Update `docs/ARCHITECTURE.md` for structural changes
- Commit messages follow `type(scope): description` format
