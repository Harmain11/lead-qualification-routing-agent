from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from threading import RLock
from typing import Protocol, cast
from uuid import UUID, uuid4

from lead_agent.domain.integrations import SecretStore
from lead_agent.settings.config import get_settings


class ManagedSecretBackend(Protocol):
    """Cloud-neutral adapter implemented by the deployment package."""

    def store(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str: ...

    def retrieve(self, reference: str) -> dict[str, str] | None: ...

    def replace(self, reference: str, values: dict[str, str]) -> str: ...

    def invalidate(self, reference: str) -> None: ...

    def delete(self, reference: str) -> None: ...


class DevelopmentSecretStore:
    """Process-local secret storage for development and tests.

    Production deployments replace this port with a managed secret store. Values are
    intentionally unavailable through connection persistence or API responses.
    """

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}
        self._invalidated: set[str] = set()
        self._lock = RLock()

    def store(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str:
        reference = f"devsecret://{workspace_id}/{provider}/{uuid4()}"
        with self._lock:
            self._values[reference] = dict(values)
            self._invalidated.discard(reference)
        return reference

    def retrieve(self, reference: str) -> dict[str, str] | None:
        with self._lock:
            if reference in self._invalidated:
                return None
            value = self._values.get(reference)
            return dict(value) if value is not None else None

    def replace(self, reference: str, values: dict[str, str]) -> str:
        with self._lock:
            if reference not in self._values or reference in self._invalidated:
                raise KeyError("secret reference is unavailable")
            self._values[reference] = dict(values)
        return reference

    def invalidate(self, reference: str) -> None:
        with self._lock:
            if reference in self._values:
                self._invalidated.add(reference)

    def delete(self, reference: str) -> None:
        with self._lock:
            self._values.pop(reference, None)
            self._invalidated.discard(reference)

    # Compatibility aliases for existing provider adapters. New lifecycle code uses
    # the explicit store/retrieve names above.
    def put(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str:
        return self.store(workspace_id, provider, values)

    def get(self, reference: str) -> dict[str, str] | None:
        return self.retrieve(reference)


class ManagedSecretStore:
    """Production SecretStore delegating to a deployment-owned managed backend.

    The application persists only the backend's opaque reference. AWS Secrets Manager,
    Azure Key Vault, Google Secret Manager, or Vault adapters can implement the backend
    protocol without changing application or provider code.
    """

    def __init__(self, backend: ManagedSecretBackend) -> None:
        self._backend = backend

    def store(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str:
        reference = self._backend.store(workspace_id, provider, dict(values))
        if not reference or reference.startswith("devsecret://"):
            raise RuntimeError("managed secret backend returned an invalid reference")
        return reference

    def retrieve(self, reference: str) -> dict[str, str] | None:
        values = self._backend.retrieve(reference)
        return dict(values) if values is not None else None

    def replace(self, reference: str, values: dict[str, str]) -> str:
        updated_reference = self._backend.replace(reference, dict(values))
        if not updated_reference or updated_reference.startswith("devsecret://"):
            raise RuntimeError("managed secret backend returned an invalid reference")
        return updated_reference

    def invalidate(self, reference: str) -> None:
        self._backend.invalidate(reference)

    def delete(self, reference: str) -> None:
        self._backend.delete(reference)

    def put(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str:
        return self.store(workspace_id, provider, values)

    def get(self, reference: str) -> dict[str, str] | None:
        return self.retrieve(reference)


def _load_managed_backend(path: str) -> ManagedSecretBackend:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError(
            "MANAGED_SECRET_STORE_BACKEND must use the form 'package.module:factory'"
        )
    try:
        factory = getattr(import_module(module_name), attribute)
        backend = factory()
    except Exception:
        raise RuntimeError("managed secret backend could not be initialized") from None
    required = {"store", "retrieve", "replace", "invalidate", "delete"}
    if any(not callable(getattr(backend, name, None)) for name in required):
        raise RuntimeError("managed secret backend does not implement the required lifecycle")
    return cast(ManagedSecretBackend, backend)


@lru_cache
def get_secret_store() -> SecretStore:
    settings = get_settings()
    if settings.secret_store_provider == "development":
        if settings.app_env == "production":
            raise RuntimeError("process-local secret storage is forbidden in production")
        return DevelopmentSecretStore()
    if settings.secret_store_provider == "managed":
        return ManagedSecretStore(_load_managed_backend(settings.managed_secret_store_backend))
    raise RuntimeError(f"unsupported secret store provider: {settings.secret_store_provider}")
