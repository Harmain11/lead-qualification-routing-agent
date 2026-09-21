from __future__ import annotations

from functools import lru_cache
from threading import RLock
from uuid import UUID, uuid4

from lead_agent.domain.integrations import SecretStore


class DevelopmentSecretStore:
    """Process-local secret storage for development and tests.

    Production deployments replace this port with a managed secret store. Values are
    intentionally unavailable through connection persistence or API responses.
    """

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}
        self._lock = RLock()

    def put(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str:
        reference = f"devsecret://{workspace_id}/{provider}/{uuid4()}"
        with self._lock:
            self._values[reference] = dict(values)
        return reference

    def get(self, reference: str) -> dict[str, str] | None:
        with self._lock:
            value = self._values.get(reference)
            return dict(value) if value is not None else None

    def delete(self, reference: str) -> None:
        with self._lock:
            self._values.pop(reference, None)


@lru_cache
def get_secret_store() -> SecretStore:
    return DevelopmentSecretStore()
