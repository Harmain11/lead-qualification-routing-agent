"""Vercel's Python Function entrypoint for the FastAPI application."""

from __future__ import annotations

import sys
from pathlib import Path

repository_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository_root / "backend" / "src"))

from lead_agent.api.main import app

__all__ = ["app"]
