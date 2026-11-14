from __future__ import annotations

import os

import httpx


def _headers(content_type: str | None = None) -> dict[str, str]:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not token:
        raise RuntimeError("Vercel Blob is not configured")
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _url(storage_key: str) -> str:
    return f"https://blob.vercel-storage.com/{storage_key}"


def put_private_attachment(storage_key: str, content: bytes, content_type: str) -> str:
    """Store workspace communication media in a Vercel private Blob store."""
    response = httpx.put(
        _url(storage_key),
        headers={**_headers(content_type), "x-add-random-suffix": "0", "x-access": "private"},
        content=content,
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json().get("pathname") or storage_key)


def get_private_attachment(storage_key: str) -> bytes:
    response = httpx.get(_url(storage_key), headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.content


def delete_private_attachment(storage_key: str) -> None:
    response = httpx.delete(_url(storage_key), headers=_headers(), timeout=30)
    response.raise_for_status()

