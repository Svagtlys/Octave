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

# Expose port
EXPOSE 8000

# Run the application
CMD ["uv", "run", "uvicorn", "octave.app:app", "--host", "0.0.0.0", "--port", "8000"]
