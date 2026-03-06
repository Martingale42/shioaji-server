FROM python:3.13-slim

# Shioaji SDK requires gcc for C extensions
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (layer cache)
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# Copy source
COPY src/ src/
RUN uv sync --no-dev

# .env, Sinopac.pfx, and server.log are runtime mounts
# Port is configured via SHIOAJI_SERVER_PORT in .env (default 8000)
ENTRYPOINT ["uv", "run", "shioaji-server"]
