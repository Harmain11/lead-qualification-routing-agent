from __future__ import annotations

from vercel.blob import sync as blob


def put_private_attachment(storage_key: str, content: bytes, content_type: str) -> str:
    """Store workspace communication media in a Vercel private Blob store."""
    uploaded = blob.put_blob(
        storage_key,
        content,
        access="private",
        content_type=content_type,
        add_random_suffix=False,
    )
    return uploaded.pathname


def get_private_attachment(storage_key: str) -> bytes:
    result = blob.get_blob(storage_key, access="private")
    return result.body


def delete_private_attachment(storage_key: str) -> None:
    blob.delete_blob(storage_key)
