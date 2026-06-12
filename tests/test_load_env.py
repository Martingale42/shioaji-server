import os
from pathlib import Path

from shioaji_server.__main__ import _load_env


def test_load_env_finds_home_shioaji_server(tmp_path, monkeypatch):
    # Snapshot os.environ so _load_env's setdefault writes don't leak into the
    # real process env (monkeypatch.undo restores the original object on teardown).
    monkeypatch.setattr(os, "environ", dict(os.environ))
    home = tmp_path / "home"
    (home / ".shioaji-server").mkdir(parents=True)
    (home / ".shioaji-server" / ".env").write_text("SHIOAJI_TEST_KEY=from_home\n")
    workdir = tmp_path / "work"  # no .env in cwd or its parent
    workdir.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.chdir(workdir)
    monkeypatch.delenv("SHIOAJI_ENV_FILE", raising=False)
    monkeypatch.delenv("SHIOAJI_TEST_KEY", raising=False)
    _load_env()
    assert os.environ.get("SHIOAJI_TEST_KEY") == "from_home"


def test_load_env_prefers_cwd_over_home(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    home = tmp_path / "home"
    (home / ".shioaji-server").mkdir(parents=True)
    (home / ".shioaji-server" / ".env").write_text("SHIOAJI_SRC=home\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / ".env").write_text("SHIOAJI_SRC=cwd\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.chdir(workdir)
    monkeypatch.delenv("SHIOAJI_ENV_FILE", raising=False)
    monkeypatch.delenv("SHIOAJI_SRC", raising=False)
    _load_env()
    assert os.environ.get("SHIOAJI_SRC") == "cwd"  # cwd wins; loop returns on first hit
