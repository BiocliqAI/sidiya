from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "output" / "extractions.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS extractions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              source_file_name TEXT NOT NULL,
              patient_name TEXT,
              primary_diagnosis TEXT,
              followup_datetime TEXT,
              extraction_json TEXT NOT NULL,
              simplified_summary TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_extraction(extracted: dict[str, Any], simplified_summary: str) -> int:
    patient_name = (
        str(extracted.get("patient", {}).get("full_name", "")).strip()
        if isinstance(extracted.get("patient"), dict)
        else ""
    )
    primary_diagnosis = (
        str(extracted.get("clinical_episode", {}).get("primary_diagnosis", "")).strip()
        if isinstance(extracted.get("clinical_episode"), dict)
        else ""
    )
    followup_datetime = None
    follow = extracted.get("follow_up", {})
    if isinstance(follow, dict):
        appointments = follow.get("appointments", [])
        if isinstance(appointments, list) and appointments:
            first = appointments[0]
            if isinstance(first, dict):
                followup_datetime = first.get("scheduled_datetime")

    source_file = ""
    source = extracted.get("source_document", {})
    if isinstance(source, dict):
        source_file = str(source.get("file_name", "")).strip()

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO extractions (
              source_file_name, patient_name, primary_diagnosis, followup_datetime,
              extraction_json, simplified_summary
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_file or "unknown.pdf",
                patient_name or None,
                primary_diagnosis or None,
                followup_datetime,
                json.dumps(extracted, ensure_ascii=True),
                simplified_summary,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_extractions(limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, source_file_name, patient_name, primary_diagnosis, followup_datetime
            FROM extractions
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()

    return [dict(row) for row in rows]


def get_extraction(extraction_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, created_at, source_file_name, patient_name, primary_diagnosis, followup_datetime,
                   extraction_json, simplified_summary
            FROM extractions
            WHERE id = ?
            """,
            (extraction_id,),
        ).fetchone()
    if row is None:
        return None

    payload = dict(row)
    payload["extraction_json"] = json.loads(payload["extraction_json"])
    return payload
