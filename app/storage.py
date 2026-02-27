"""Storage layer — now backed by Firestore (was SQLite).

This module preserves the same public API so the rest of the codebase
(main.py, app.js, history.js, etc.) continues to work unchanged.
The only semantic difference: IDs are now Firestore document‐ID strings
instead of auto-increment integers.
"""
from __future__ import annotations

from typing import Any

from app import firestore_client as fdb


def init_db() -> None:
    """No-op kept for backward compatibility with startup_event()."""
    pass


def save_extraction(extracted: dict[str, Any], simplified_summary: str) -> str:
    """Persist an extraction and return the Firestore document ID (str)."""
    return fdb.save_extraction(extracted, simplified_summary)


def list_extractions(limit: int = 100) -> list[dict[str, Any]]:
    """Return recent extractions (lightweight listing, no JSON blob)."""
    return fdb.list_extractions(limit=limit)


def get_extraction(extraction_id: str) -> dict[str, Any] | None:
    """Fetch a full extraction record by Firestore document ID."""
    return fdb.get_extraction(extraction_id)
