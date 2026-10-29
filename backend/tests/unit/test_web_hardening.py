from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient

from lead_agent.api.main import CachedStaticFiles
from lead_agent.api.middleware import install_request_middleware


def test_csp_restricts_scripts_and_connections() -> None:
    app = FastAPI()
    install_request_middleware(app, cast(Any, SimpleNamespace(app_env="production")))
    app.get("/ok")(lambda: {"ok": True})
    with TestClient(app) as client:
        policy = client.get("/ok").headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self' https://i.replit.com;" in policy
    assert "'unsafe-inline' https://cdn" not in policy
    assert "connect-src 'self'" in policy


def test_static_assets_are_cacheable_and_html_revalidates(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1);" * 200)
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.mount("/", CachedStaticFiles(directory=str(tmp_path), html=True), name="frontend")
    with TestClient(app) as client:
        asset = client.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
        page = client.get("/")
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert asset.headers["Content-Encoding"] == "gzip"
    assert page.headers["Cache-Control"] == "no-cache"
