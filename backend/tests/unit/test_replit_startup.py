from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def startup_runner(tmp_path: Path, repository_root: Path):
    """Exercise the real shell runner without live providers, jobs, or secrets."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(repository_root / "scripts/replit_start.sh", scripts / "replit_start.sh")
    frontend = tmp_path / "frontend/dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<html></html>")
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        f"#!{sys.executable}\n"
        """
import os
import signal
import sys
import time
from pathlib import Path

root = Path(os.environ["TEST_ROOT"])
events = root / "events"
def record(event):
    with events.open("a") as handle:
        handle.write(event + "\\n")

def run_service(name):
    record(name)
    (root / (name + ".started")).touch()
    def stop(*args):
        record(name + ".stopped")
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    while True:
        time.sleep(0.01)

if sys.argv[1:3] == ["-m", "lead_agent.production_check"]:
    record("config")
    sys.exit(1 if os.environ.get("TEST_FAILURE") == "config" else 0)
elif sys.argv[1:3] == ["-m", "uvicorn"]:
    assert "--port" in sys.argv and sys.argv[sys.argv.index("--port") + 1] == "5000"
    run_service("api")
elif sys.argv[1] == "-":
    code = sys.stdin.read()
    check_mode = os.environ.get("TEST_CHECK") == "1"
    if not check_mode:
        deadline = time.monotonic() + 3
        while not (root / "api.started").exists():
            if time.monotonic() >= deadline:
                sys.exit(2)
            time.sleep(0.01)
    probe = "gmail" if "GmailEmailProvider" in code else "database"
    record(probe)
    sys.exit(1 if os.environ.get("TEST_FAILURE") == probe else 0)
elif sys.argv[1:3] == ["-m", "celery"]:
    assert "database" in events.read_text().splitlines()
    if "worker" in sys.argv:
        record("worker")
        time.sleep(0.2)
        sys.exit(0)  # An unexpected clean exit must still stop the API.
    run_service("beat")
else:
    sys.exit(2)
"""
    )
    fake_python.chmod(0o755)

    def run(*, check: bool = False, failure: str = ""):
        env = {
            "PATH": os.environ["PATH"],
            "PYTHON_BIN": str(fake_python),
            "SESSION_SECRET": "test-only-not-a-real-secret",
            "SECRET_STORE_ENCRYPTION_KEY": "test-only-not-a-real-key",
            "REDIS_DEPLOYMENT_MODE": "remote",
            "EMAIL_PROVIDER": "gmail",
            "CELERY_BEAT_LOCK_FILE": str(tmp_path / "beat.lock"),
            "CELERY_BEAT_SCHEDULE_FILE": str(tmp_path / "beat.schedule"),
            "TEST_ROOT": str(tmp_path),
            "TEST_CHECK": "1" if check else "0",
            "TEST_FAILURE": failure,
        }
        args = ["bash", str(scripts / "replit_start.sh")]
        if check:
            args.append("--check")
        result = subprocess.run(
            args, env=env, capture_output=True, text=True, timeout=10, check=False,
        )
        events = tmp_path / "events"
        return result, events.read_text().splitlines()

    return run


def test_api_starts_before_remote_probes_and_workers_wait(startup_runner):
    result, events = startup_runner()
    assert result.returncode == 1
    assert events[:4] == ["config", "api", "gmail", "database"]
    assert events.index("worker") > events.index("database")
    assert "api.stopped" in events
    assert "beat.stopped" in events


def test_preflight_never_starts_api_or_workers(startup_runner):
    result, events = startup_runner(check=True)
    assert result.returncode == 0
    assert events == ["config", "gmail", "database"]


@pytest.mark.parametrize("failure", ["gmail", "database"])
def test_failed_probe_stops_api_without_starting_workers(startup_runner, failure):
    result, events = startup_runner(failure=failure)
    assert result.returncode == 1
    assert "api.stopped" in events
    assert "worker" not in events and "beat" not in events


def test_invalid_configuration_never_starts_api(startup_runner):
    result, events = startup_runner(failure="config")
    assert result.returncode == 1
    assert events == ["config"]
