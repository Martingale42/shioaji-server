# Runtime Artifact Consolidation (`~/.shioaji-server/`) Implementation Plan

> **For Claude:** Use superpowers:executing-plans, superpowers:subagent-driven-development, or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Move all scattered `shioaji-server` runtime artifacts (credentials, env file, logs) out of the repo tree and the home root into a single unified `~/.shioaji-server/` directory, mirroring the `~/.claude/` convention.

**Architecture:** The gateway runs as a Docker container that bind-mounts the host's `.env` / `Sinopac.pfx` / `server.log` into `/app/`. The in-container paths and `CA_PATH=/app/Sinopac.pfx` stay **unchanged**; only the **host side** of the mounts moves. The two cron shell scripts write logs to fixed `$HOME` paths that move under `~/.shioaji-server/logs/`. The data CLI needs no credentials (HTTP-only to the gateway), so the blast radius is narrow. One code change is needed so local (non-Docker) runs can still discover the relocated `.env`.

**Tech Stack:** Docker bind-mounts, GNU Make, Bash, Python 3 (`uv run`), FastAPI gateway.

---

## Target Layout

```
~/.shioaji/                     # LEFT UNTOUCHED — owned by the shioaji library (contract cache)
└── contracts-1.3.2.pkl

~/.shioaji-server/              # NEW unified artifact root (chmod 700)
├── .env                        # moved from repo root        (chmod 600)
├── Sinopac.pfx                 # moved from repo root        (chmod 600)
└── logs/
    ├── server.log              # gateway log (Docker bind-mount target)
    ├── shioaji.log             # stale lib log moved for tidiness (optional)
    ├── 0050_fetch/             # was ~/shioaji_0050_fetch_logs/
    └── market_open_verify/     # was ~/sinopac_market_open_logs/
```

## Critical Constraints (read before editing)

1. **DO NOT change the `SHIOAJI_LOG_FILE` default in `src/shioaji_server/__main__.py`** (currently `"server.log"`). Inside the container `WORKDIR=/app` and the process runs as **root**, so a `~`-based default would expand to `/root/.shioaji-server/...` — a path that is **not bind-mounted**, silently dropping all gateway logs. The relative `"server.log"` is correct: it resolves to `/app/server.log` and the Makefile mount maps it to the host. Host log location is controlled by the **Makefile mount source**, not by this default.
2. **In-container paths stay fixed:** `-v ...:/app/.env`, `-v ...:/app/Sinopac.pfx`, `-v ...:/app/server.log`, and `-e CA_PATH=/app/Sinopac.pfx`. Only the host-side mount **sources** (the Makefile `*_FILE` vars) change. **No image rebuild required.**
3. **`.env` / `Sinopac.pfx` / `server.log` are gitignored** — moving them is a host filesystem operation, never a git change. Only Makefile, scripts, `__main__.py`, `.env.example`, and docs get committed.
4. **Keep repo-root originals as backup** until the restarted container is verified healthy (Task 6). Only then delete them (Task 7).

## Execution Setup

Run all git work on a fresh branch off `main`, ideally in a dedicated worktree:

```bash
cd /home/cy/Code/MT5/shioaji-server
git switch -c artifact-dir-consolidation
```

All paths below are absolute or relative to `/home/cy/Code/MT5/shioaji-server`.

---

### Task 1: Create the artifact directory and migrate files (host-side, no git)

**Files:** none committed — pure host filesystem operations.

**Implementation:**

Create the structure, **copy** credentials (keep originals as backup for now), and **move** the existing log directories.

```bash
mkdir -p ~/.shioaji-server/logs/0050_fetch
mkdir -p ~/.shioaji-server/logs/market_open_verify
chmod 700 ~/.shioaji-server

# Credentials: COPY (do not delete originals until Task 7 verification passes)
cp -p .env            ~/.shioaji-server/.env
cp -p Sinopac.pfx     ~/.shioaji-server/Sinopac.pfx
chmod 600 ~/.shioaji-server/.env ~/.shioaji-server/Sinopac.pfx

# Logs: move existing run logs into the consolidated tree
mv ~/shioaji_0050_fetch_logs/*       ~/.shioaji-server/logs/0050_fetch/      2>/dev/null || true
mv ~/sinopac_market_open_logs/*      ~/.shioaji-server/logs/market_open_verify/ 2>/dev/null || true
# Stale repo-root lib log (dated, orphaned) — keep a copy under logs/ for tidiness
cp -p shioaji.log ~/.shioaji-server/logs/shioaji.log 2>/dev/null || true
```

**Verification:**

Run:
```bash
ls -la ~/.shioaji-server ~/.shioaji-server/logs ~/.shioaji-server/logs/0050_fetch | sed 's/  */ /g'
stat -c '%a %n' ~/.shioaji-server ~/.shioaji-server/.env ~/.shioaji-server/Sinopac.pfx
```
Expected: `~/.shioaji-server` is `700`, `.env` and `Sinopac.pfx` are `600`, and the moved `run-*.log` / `verify_*.log` files appear under `logs/0050_fetch/` and `logs/market_open_verify/`.

**Commit:** none (host-only).

---

### Task 2: Point the Makefile mount sources at `~/.shioaji-server/`

**Files:**
- Modify: `Makefile`

**Implementation:**

Change the three host-side path variables and ensure the `logs/` directory exists before `touch`. Leave every `-v ...:/app/...` line and `-e CA_PATH=/app/Sinopac.pfx` untouched.

```makefile
# Paths (now under the consolidated ~/.shioaji-server/ artifact dir)
ENV_FILE := $(HOME)/.shioaji-server/.env
CA_FILE  := $(HOME)/.shioaji-server/Sinopac.pfx
LOG_FILE := $(HOME)/.shioaji-server/logs/server.log
```

And update the `_ensure-log` helper so the logs dir is created first:

```makefile
.PHONY: _ensure-log
_ensure-log:
	@mkdir -p $(dir $(LOG_FILE))
	@touch $(LOG_FILE)
```

The PORT-reading line (`grep ... $(ENV_FILE)`) and `_ensure-env`'s `test -f $(ENV_FILE)` / `test -f $(CA_FILE)` checks need no edit — they now resolve to the new locations automatically.

**Verification:**

Run:
```bash
make -n up | grep -E 'shioaji-server/(\.env|Sinopac\.pfx|logs/server\.log)'
```
Expected: the printed (dry-run) `docker run -v` sources all point under `/home/cy/.shioaji-server/`, while their `:/app/...` targets are unchanged.

**Commit:**
```bash
git add Makefile
git commit -m "ops: point container mount sources at ~/.shioaji-server"
```

---

### Task 3: Relocate cron-script log directories

**Files:**
- Modify: `scripts/resume_0050_fetch.sh`
- Modify: `scripts/market_open_verify.sh`

**Implementation:**

One line each (the `mkdir -p "$LOGDIR"` directly below already creates the new path).

`scripts/resume_0050_fetch.sh`:
```bash
LOGDIR="$HOME/.shioaji-server/logs/0050_fetch"
```

`scripts/market_open_verify.sh`:
```bash
LOGDIR="$HOME/.shioaji-server/logs/market_open_verify"
```

No crontab edit is needed: both cron lines call the scripts by absolute path and redirect to `/dev/null`.

**Verification:**

Run:
```bash
grep -n 'LOGDIR=' scripts/resume_0050_fetch.sh scripts/market_open_verify.sh
bash -n scripts/resume_0050_fetch.sh && bash -n scripts/market_open_verify.sh && echo "syntax ok"
```
Expected: both `LOGDIR` lines show the new `~/.shioaji-server/logs/...` paths; syntax check passes.

**Commit:**
```bash
git add scripts/resume_0050_fetch.sh scripts/market_open_verify.sh
git commit -m "ops: write cron script logs under ~/.shioaji-server/logs"
```

---

### Task 4: Let local (non-Docker) runs discover the relocated `.env`

**Files:**
- Modify: `src/shioaji_server/__main__.py`
- Test: `tests/test_load_env.py` (new, small)

**Implementation:**

Extend the `_load_env()` search chain with a `~/.shioaji-server/.env` fallback so `make local` / `uv run shioaji-server` still finds credentials after the move. Docker is unaffected: `cwd` is `/app` and `/app/.env` (mounted) matches first.

```python
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
            ...  # unchanged body
            return
```

**Do NOT touch** the `SHIOAJI_LOG_FILE` default (`"server.log"`) — see Critical Constraint #1.

**Tests:** Required — `_load_env` is the credential-discovery entry point and this is a behavior change.

```python
# tests/test_load_env.py
import os
from pathlib import Path
from shioaji_server.__main__ import _load_env

def test_load_env_finds_home_shioaji_server(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".shioaji-server").mkdir(parents=True)
    (home / ".shioaji-server" / ".env").write_text("SHIOAJI_TEST_KEY=from_home\n")
    workdir = tmp_path / "work"            # no .env in cwd or parent
    workdir.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(workdir)
    monkeypatch.delenv("SHIOAJI_ENV_FILE", raising=False)
    monkeypatch.delenv("SHIOAJI_TEST_KEY", raising=False)
    _load_env()
    assert os.environ.get("SHIOAJI_TEST_KEY") == "from_home"
```

**Verification:**

Run: `uv run pytest tests/test_load_env.py -v`
Expected: test passes (the `~/.shioaji-server/.env` candidate is reached when cwd/parent lack a `.env`).

**Commit:**
```bash
git add src/shioaji_server/__main__.py tests/test_load_env.py
git commit -m "feat: discover .env under ~/.shioaji-server for local runs"
```

---

### Task 5: Update `CA_PATH` in the live `.env` and the tracked `.env.example`

**Files:**
- Modify (host, gitignored): `~/.shioaji-server/.env`
- Modify (tracked): `.env.example`

**Implementation:**

The container ignores `.env`'s `CA_PATH` (overridden by `-e CA_PATH=/app/Sinopac.pfx`), so this only affects `make local`. Point it at the new host cert.

In `~/.shioaji-server/.env`, change:
```
CA_PATH="/home/cy/.shioaji-server/Sinopac.pfx"
```

In tracked `.env.example`, change the example line to reflect the new convention:
```
CA_PATH=/home/<you>/.shioaji-server/Sinopac.pfx    # local runs only; Docker uses /app/Sinopac.pfx via mount
```

**Verification:**

Run:
```bash
grep -n '^CA_PATH=' ~/.shioaji-server/.env .env.example
```
Expected: live `.env` points to `~/.shioaji-server/Sinopac.pfx`; `.env.example` shows the new convention with the Docker note.

**Commit:**
```bash
git add .env.example
git commit -m "docs: update .env.example CA_PATH to ~/.shioaji-server convention"
```

---

### Task 6: Restart the gateway and verify end-to-end

**Files:** none.

**Implementation:**

Restart so the container picks up the new mount sources, then verify health, login, and that the gateway log now lands in the consolidated location.

```bash
make restart                 # down → up, re-mounts from ~/.shioaji-server/
```

**Verification (all must pass):**

1. Health + session up:
   ```bash
   curl -s http://localhost:8123/api/health
   ```
   Expected: `{"status":"ok","connected":true,"logged_in":true,"session_alive":true}`

2. Mounts now sourced from the new dir:
   ```bash
   docker inspect shioaji --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
   ```
   Expected: sources are `/home/cy/.shioaji-server/.env`, `/home/cy/.shioaji-server/Sinopac.pfx`, `/home/cy/.shioaji-server/logs/server.log`.

3. Gateway log is being written to the new path (and NOT to the old repo-root file):
   ```bash
   ls -la ~/.shioaji-server/logs/server.log
   tail -n 3 ~/.shioaji-server/logs/server.log
   ```
   Expected: file exists, recently modified, contains fresh startup log lines.

4. Data path still works (real gateway probe through the CLI):
   ```bash
   uv run shioaji-data inspect --catalog ./catalog | tail -n 5
   ```
   Expected: inspect prints the catalog summary (gateway reachable, no `GatewayStaleError`).

**Commit:** none (operational verification).

---

### Task 7: Remove the now-redundant repo-root originals (after Task 6 passes)

**Files:** none committed (all gitignored host files).

**Implementation:**

Only after the restarted container is verified healthy, delete the backup originals and the emptied old log dirs.

```bash
cd /home/cy/Code/MT5/shioaji-server
rm -f .env Sinopac.pfx server.log shioaji.log
rmdir ~/shioaji_0050_fetch_logs ~/sinopac_market_open_logs 2>/dev/null || true
```

**Verification:**

Run:
```bash
ls -la .env Sinopac.pfx server.log shioaji.log 2>&1 | grep -c 'No such file' && echo "repo root clean"
git status --porcelain        # should show no tracked deletions (these were gitignored)
curl -s http://localhost:8123/api/health
```
Expected: repo root no longer holds any credential/log file; `git status` is clean (no tracked changes from the deletions); gateway still healthy (the running container holds its mount inodes, but a final `make restart` may be run to re-confirm a clean start from the new sources only).

**Commit:** none.

---

### Task 8: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/REFERENCE.md`
- Modify: `docs/ARCHITECTURE.md`

**Implementation:**

Replace stale path references with the new convention. Use grep to locate (line numbers drift). Leave historical docs untouched: `docs/plans/*`, `docs/AUDIT.md`, `docs/sessions/`, `docs/qa/`, `docs/reviews/`, `docs/BACKLOG.md`.

Find every occurrence to update:
```bash
grep -rnE 'cp .env.example .env|tail -f server\.log|server\.log|Sinopac\.pfx|\.env|shioaji_0050_fetch_logs|sinopac_market_open_logs|CA_PATH' \
  README.md docs/REFERENCE.md docs/ARCHITECTURE.md
```

Apply these substitutions (keep in-container `/app/...` mentions unchanged):
- Setup: `cp .env.example .env` → place at `~/.shioaji-server/.env`; "put `Sinopac.pfx` in `shioaji-server/`" → "put `Sinopac.pfx` in `~/.shioaji-server/`".
- Ops: `tail -f server.log` → `tail -f ~/.shioaji-server/logs/server.log`; `uv run shioaji-server &> server.log &` → `... &> ~/.shioaji-server/logs/server.log &`.
- Env-var docs: note `SHIOAJI_ENV_FILE` now also defaults-searches `~/.shioaji-server/.env`; `SHIOAJI_LOG_FILE` host default is `~/.shioaji-server/logs/server.log` (via Docker mount; in-container value stays `server.log`).
- ARCHITECTURE mount diagram: annotate host paths as `~/.shioaji-server/{.env,Sinopac.pfx,logs/server.log}` → container `/app/{.env,Sinopac.pfx,server.log}`.
- Add a short "Runtime artifacts" subsection to README documenting the `~/.shioaji-server/` layout and that `~/.shioaji/` belongs to the upstream library.

**Verification:**

Run:
```bash
grep -rnE 'tail -f server\.log|cp \.env\.example \.env|shioaji_0050_fetch_logs|sinopac_market_open_logs' \
  README.md docs/REFERENCE.md docs/ARCHITECTURE.md
```
Expected: no remaining stale references (empty output) in the three active docs.

**Commit:**
```bash
git add README.md docs/REFERENCE.md docs/ARCHITECTURE.md
git commit -m "docs: document ~/.shioaji-server runtime artifact dir"
```

---

## Rollback

If anything breaks after `make restart`, revert by pointing the Makefile vars back at `$(CURDIR)` and restoring the still-present repo-root originals (kept until Task 7):

```bash
git checkout Makefile scripts/ src/shioaji_server/__main__.py
make restart        # originals at repo root are still intact pre-Task-7
```

Once Task 7 has deleted the originals, rollback instead means copying back from `~/.shioaji-server/` to the repo root before reverting the Makefile.

## Out of Scope (YAGNI)

- No image rebuild (in-container paths unchanged).
- No change to the `~/.shioaji/` library cache.
- No XDG `$XDG_CONFIG_HOME` / `$XDG_STATE_HOME` split — a single `~/.shioaji-server/` is sufficient for this single-host setup.
- No migration of the external NautilusTrader repo that `market_open_verify.sh` drives (only its log output dir moves).
