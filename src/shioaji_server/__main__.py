import logging
import os
import sys
from pathlib import Path

import uvicorn


def _load_env() -> None:
    """Load .env file, searching current dir then parent dirs up to 2 levels."""
    explicit = os.environ.get("SHIOAJI_ENV_FILE")
    if explicit:
        candidates = [Path(explicit)]
    else:
        cwd = Path.cwd()
        candidates = [cwd / ".env", cwd.parent / ".env"]

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


def main():
    simulation = "--live" not in sys.argv
    os.environ.setdefault("SHIOAJI_SIMULATION", str(simulation).lower())

    _load_env()

    host = os.environ.get("SHIOAJI_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SHIOAJI_SERVER_PORT", "8000"))

    log_level = os.environ.get("SHIOAJI_LOG_LEVEL", "info").lower()
    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))

    uvicorn.run(
        "shioaji_server.app:app",
        host=host,
        port=port,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
