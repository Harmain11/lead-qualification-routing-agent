from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session, sessionmaker

from lead_agent.infrastructure.db.models import EncryptedProviderSecretORM
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.settings.config import get_settings

REFERENCE_PREFIX = "dbsecret://"


def _decode_key(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        key = base64.urlsafe_b64decode(value + padding)
    except Exception:
        raise RuntimeError("secret-store encryption key is not valid base64url") from None
    if len(key) != 32:
        raise RuntimeError("secret-store encryption key must decode to exactly 32 bytes")
    return key


def _reference_id(reference: str) -> UUID:
    if not reference.startswith(REFERENCE_PREFIX):
        raise KeyError("invalid encrypted secret reference")
    try:
        return UUID(reference.removeprefix(REFERENCE_PREFIX))
    except ValueError:
        raise KeyError("invalid encrypted secret reference") from None


class EncryptedDatabaseSecretBackend:
    """Cloud-neutral persistent secret storage using AES-256-GCM envelopes.

    The database stores ciphertext, a random nonce, and a non-secret key version. Master
    keys remain deployment-owned. Previous keys permit online rotation; every replace
    re-encrypts with the active key.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        active_key_version: str,
        active_key: bytes,
        previous_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        if len(active_key) != 32:
            raise ValueError("active encryption key must contain 32 bytes")
        if not active_key_version.strip():
            raise ValueError("active encryption key version is required")
        self._session_factory = session_factory
        self._active_key_version = active_key_version
        self._keys = {**dict(previous_keys or {}), active_key_version: active_key}
        if any(len(key) != 32 for key in self._keys.values()):
            raise ValueError("every encryption key must contain 32 bytes")

    @staticmethod
    def _aad(secret_id: UUID, workspace_id: UUID, provider: str, key_version: str) -> bytes:
        return f"{secret_id}:{workspace_id}:{provider}:{key_version}".encode()

    def _encrypt(
        self, secret_id: UUID, workspace_id: UUID, provider: str, values: dict[str, str]
    ) -> str:
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ciphertext = AESGCM(self._keys[self._active_key_version]).encrypt(
            nonce,
            plaintext,
            self._aad(secret_id, workspace_id, provider, self._active_key_version),
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def _decrypt(self, row: EncryptedProviderSecretORM) -> dict[str, str]:
        key = self._keys.get(row.key_version)
        if key is None:
            raise RuntimeError("secret-store key version is unavailable")
        try:
            packed = base64.urlsafe_b64decode(row.ciphertext)
            plaintext = AESGCM(key).decrypt(
                packed[:12],
                packed[12:],
                self._aad(row.id, row.workspace_id, row.provider, row.key_version),
            )
            values = json.loads(plaintext)
        except Exception:
            raise RuntimeError("encrypted secret could not be decrypted") from None
        if not isinstance(values, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in values.items()
        ):
            raise RuntimeError("encrypted secret payload is invalid")
        return values

    def store(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str:
        secret_id = uuid4()
        now = datetime.now(UTC)
        row = EncryptedProviderSecretORM(
            id=secret_id,
            workspace_id=workspace_id,
            provider=provider,
            ciphertext=self._encrypt(secret_id, workspace_id, provider, values),
            key_version=self._active_key_version,
            invalidated_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
        return f"{REFERENCE_PREFIX}{secret_id}"

    def retrieve(self, reference: str) -> dict[str, str] | None:
        secret_id = _reference_id(reference)
        with self._session_factory() as session:
            row = session.get(EncryptedProviderSecretORM, secret_id)
            if row is None or row.invalidated_at is not None:
                return None
            return self._decrypt(row)

    def retrieve_for_workspace(
        self, workspace_id: UUID, provider: str, reference: str
    ) -> dict[str, str] | None:
        secret_id = _reference_id(reference)
        with self._session_factory() as session:
            row = session.get(EncryptedProviderSecretORM, secret_id)
            if (
                row is None
                or row.invalidated_at is not None
                or row.workspace_id != workspace_id
                or row.provider != provider
            ):
                return None
            return self._decrypt(row)

    def replace(self, reference: str, values: dict[str, str]) -> str:
        secret_id = _reference_id(reference)
        with self._session_factory() as session:
            row = session.get(EncryptedProviderSecretORM, secret_id)
            if row is None or row.invalidated_at is not None:
                raise KeyError("secret reference is unavailable")
            return self._replace_row(session, row, values, reference)

    def replace_for_workspace(
        self, workspace_id: UUID, provider: str, reference: str, values: dict[str, str]
    ) -> str:
        secret_id = _reference_id(reference)
        with self._session_factory() as session:
            row = session.get(EncryptedProviderSecretORM, secret_id)
            if (
                row is None
                or row.invalidated_at is not None
                or row.workspace_id != workspace_id
                or row.provider != provider
            ):
                raise KeyError("secret reference does not belong to this workspace provider")
            return self._replace_row(session, row, values, reference)

    def _replace_row(
        self,
        session: Session,
        row: EncryptedProviderSecretORM,
        values: dict[str, str],
        reference: str,
    ) -> str:
        row.key_version = self._active_key_version
        row.ciphertext = self._encrypt(row.id, row.workspace_id, row.provider, values)
        row.updated_at = datetime.now(UTC)
        session.commit()
        return reference

    def invalidate(self, reference: str) -> None:
        secret_id = _reference_id(reference)
        with self._session_factory() as session:
            row = session.get(EncryptedProviderSecretORM, secret_id)
            if row is not None and row.invalidated_at is None:
                row.invalidated_at = datetime.now(UTC)
                row.updated_at = row.invalidated_at
                session.commit()

    def delete(self, reference: str) -> None:
        secret_id = _reference_id(reference)
        with self._session_factory() as session:
            row = session.get(EncryptedProviderSecretORM, secret_id)
            if row is not None:
                session.delete(row)
                session.commit()


def create_backend() -> EncryptedDatabaseSecretBackend:
    settings = get_settings()
    try:
        previous_raw = json.loads(settings.secret_store_previous_keys)
    except json.JSONDecodeError:
        raise RuntimeError("SECRET_STORE_PREVIOUS_KEYS must be a JSON object") from None
    if not isinstance(previous_raw, dict) or any(
        not isinstance(version, str) or not isinstance(value, str)
        for version, value in previous_raw.items()
    ):
        raise RuntimeError("SECRET_STORE_PREVIOUS_KEYS must map versions to base64url keys")
    previous = {version: _decode_key(value) for version, value in previous_raw.items()}
    return EncryptedDatabaseSecretBackend(
        get_session_factory(),
        active_key_version=settings.secret_store_key_version,
        active_key=_decode_key(settings.secret_store_encryption_key),
        previous_keys=previous,
    )
