"""Vercel Celery subscriber entrypoint."""

from lead_agent.worker.celery_app import celery_app as app

__all__ = ["app"]
