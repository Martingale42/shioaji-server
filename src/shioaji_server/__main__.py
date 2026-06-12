import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn


def _load_env() -> None:
    """Load .env, searching: SHIOAJI_ENV_FILE → cwd → parent → ~/.shioaji-server/.env."""
    explicit = os.environ.get("SHIOAJI_ENV_FILE")
    if explicit:
        candidates = [Path(explicit)]
    else:
        cwd = Path.cwd()
        candidates = [
            cwd / ".env",
            cwd.parent / ".env",
            Path.home() / ".shioaji-server" / ".env",
        ]

    for path in candidates:
        if path.is_file():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                if key and value:
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


def _configure_logging(log_level: str) -> None:
    """Configure root logging with console + a rotating file handler.

    The file handler writes to ``SHIOAJI_LOG_FILE`` (default ``server.log``,
    bind-mounted to the host in Docker), so session-down / re-login events and
    errors survive container restarts. Previously the gateway logged only to
    stdout → docker logs, which were lost when the container was recreated,
    making intermittent deaths impossible to diagnose after the fact.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    log_file = os.environ.get("SHIOAJI_LOG_FILE", "server.log")
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(
            RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=3)
        )
    except OSError:
        logging.getLogger(__name__).warning(
            "Cannot open log file %s — console logging only", log_file
        )
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def main():
    simulation = "--live" not in sys.argv
    os.environ.setdefault("SHIOAJI_SIMULATION", str(simulation).lower())

    _load_env()

    host = os.environ.get("SHIOAJI_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SHIOAJI_SERVER_PORT", "8123"))

    log_level = os.environ.get("SHIOAJI_LOG_LEVEL", "info").lower()
    _configure_logging(log_level)

    uvicorn.run(
        "shioaji_server.app:app",
        host=host,
        port=port,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
