"""Firestore client for the Sidiya Reminder System.

Manages patients, reminder rules, vital logs, medication logs,
daily compliance, escalations, and providers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import FieldFilter

from app.config import settings

logger = logging.getLogger(__name__)

_db = None


def _get_db():
    """Lazy-initialize Firebase Admin SDK and return Firestore client.

    Uses key file locally (firebase-admin-key.json) or Application Default
    Credentials in Cloud Run (no key file needed).
    """
    global _db
    if _db is not None:
        return _db

    if not firebase_admin._apps:
        import os
        key_path = settings.firebase_credentials_path
        if key_path and os.path.exists(key_path):
            # Local development: use key file
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id})
        else:
            # Cloud Run: use Application Default Credentials
            firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})

    _db = firestore.client()
    return _db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _now_utc().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------

def create_patient(data: dict[str, Any]) -> str:
    """Create a new patient document. Returns the patient ID."""
    db = _get_db()
    data.setdefault("status", "active")
    data.setdefault("notification_preferences", {"sms": True, "whatsapp": False, "push": True})
    data.setdefault("device_tokens", [])
    data.setdefault("created_at", _now_utc())
    data.setdefault("updated_at", _now_utc())
    _, doc_ref = db.collection("patients").add(data)
    return doc_ref.id


def get_patient(patient_id: str) -> dict[str, Any] | None:
    """Fetch a patient by ID."""
    db = _get_db()
    doc = db.collection("patients").document(patient_id).get()
    if not doc.exists:
        return None
    result = doc.to_dict()
    result["id"] = doc.id
    return result


def get_patient_by_phone(phone: str) -> dict[str, Any] | None:
    """Fetch a patient by phone number."""
    db = _get_db()
    docs = (
        db.collection("patients")
        .where(filter=FieldFilter("phone", "==", phone))
        .limit(1)
        .get()
    )
    for doc in docs:
        result = doc.to_dict()
        result["id"] = doc.id
        return result
    return None


def get_patient_by_extraction(extraction_id: str) -> dict[str, Any] | None:
    """Fetch a patient by their extraction_id."""
    db = _get_db()
    docs = (
        db.collection("patients")
        .where(filter=FieldFilter("extraction_id", "==", extraction_id))
        .limit(1)
        .get()
    )
    for doc in docs:
        result = doc.to_dict()
        result["id"] = doc.id
        return result
    return None


def update_patient(patient_id: str, data: dict[str, Any]) -> None:
    """Update patient fields."""
    db = _get_db()
    data["updated_at"] = _now_utc()
    db.collection("patients").document(patient_id).update(data)


def list_active_patients() -> list[dict[str, Any]]:
    """List all patients with status 'active'."""
    db = _get_db()
    docs = (
        db.collection("patients")
        .where(filter=FieldFilter("status", "==", "active"))
        .get()
    )
    results = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Reminder Rules
# ---------------------------------------------------------------------------

def add_reminder_rule(patient_id: str, rule: dict[str, Any]) -> str:
    """Add a reminder rule for a patient. Returns the rule ID."""
    db = _get_db()
    rule.setdefault("active", True)
    rule.setdefault("created_from", "extraction_pipeline")
    _, doc_ref = (
        db.collection("patients")
        .document(patient_id)
        .collection("reminder_rules")
        .add(rule)
    )
    return doc_ref.id


def get_reminder_rules(patient_id: str, active_only: bool = True) -> list[dict[str, Any]]:
    """Get all reminder rules for a patient."""
    db = _get_db()
    query = db.collection("patients").document(patient_id).collection("reminder_rules")
    if active_only:
        query = query.where(filter=FieldFilter("active", "==", True))
    docs = query.get()
    results = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Vital Logs
# ---------------------------------------------------------------------------

def log_vital(patient_id: str, vital_type: str, value: Any, source: str = "patient_app") -> str:
    """Log a vital sign (weight, bp, heart_rate, spo2, symptom_check)."""
    db = _get_db()
    data = {
        "type": vital_type,
        "value": value,
        "logged_at": _now_utc(),
        "source": source,
        "date": _today_iso(),
    }
    _, doc_ref = (
        db.collection("patients")
        .document(patient_id)
        .collection("vital_logs")
        .add(data)
    )
    return doc_ref.id


def get_vitals_for_date(patient_id: str, date_iso: str, vital_type: str | None = None) -> list[dict[str, Any]]:
    """Get vital logs for a patient on a specific date."""
    db = _get_db()
    query = (
        db.collection("patients")
        .document(patient_id)
        .collection("vital_logs")
        .where(filter=FieldFilter("date", "==", date_iso))
    )
    if vital_type:
        query = query.where(filter=FieldFilter("type", "==", vital_type))
    docs = query.get()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def get_vitals_range(patient_id: str, vital_type: str, days: int = 7) -> list[dict[str, Any]]:
    """Get vital logs for the last N days."""
    db = _get_db()
    cutoff = (_now_utc() - timedelta(days=days)).strftime("%Y-%m-%d")
    docs = (
        db.collection("patients")
        .document(patient_id)
        .collection("vital_logs")
        .where(filter=FieldFilter("type", "==", vital_type))
        .where(filter=FieldFilter("date", ">=", cutoff))
        .order_by("date")
        .get()
    )
    return [{"id": d.id, **d.to_dict()} for d in docs]


# ---------------------------------------------------------------------------
# Medication Logs
# ---------------------------------------------------------------------------

def log_medication(patient_id: str, medication_name: str, scheduled_time: str, status: str = "taken", skip_reason: str | None = None) -> str:
    """Log a medication acknowledgment."""
    db = _get_db()
    data = {
        "medication_name": medication_name,
        "scheduled_time": scheduled_time,
        "acknowledged_at": _now_utc() if status == "taken" else None,
        "status": status,
        "skip_reason": skip_reason,
        "date": _today_iso(),
    }
    _, doc_ref = (
        db.collection("patients")
        .document(patient_id)
        .collection("medication_logs")
        .add(data)
    )
    return doc_ref.id


def get_medication_logs_for_date(patient_id: str, date_iso: str) -> list[dict[str, Any]]:
    """Get medication logs for a patient on a specific date."""
    db = _get_db()
    docs = (
        db.collection("patients")
        .document(patient_id)
        .collection("medication_logs")
        .where(filter=FieldFilter("date", "==", date_iso))
        .get()
    )
    return [{"id": d.id, **d.to_dict()} for d in docs]


# ---------------------------------------------------------------------------
# Daily Compliance
# ---------------------------------------------------------------------------

def update_daily_compliance(patient_id: str, date_iso: str, data: dict[str, Any]) -> None:
    """Create or update the daily compliance document for a patient."""
    db = _get_db()
    data["computed_at"] = _now_utc()
    (
        db.collection("patients")
        .document(patient_id)
        .collection("daily_compliance")
        .document(date_iso)
        .set(data, merge=True)
    )


def get_daily_compliance(patient_id: str, date_iso: str) -> dict[str, Any] | None:
    """Get the compliance record for a specific date."""
    db = _get_db()
    doc = (
        db.collection("patients")
        .document(patient_id)
        .collection("daily_compliance")
        .document(date_iso)
        .get()
    )
    if not doc.exists:
        return None
    return doc.to_dict()


def get_compliance_range(patient_id: str, days: int = 7) -> list[dict[str, Any]]:
    """Get compliance records for the last N days."""
    db = _get_db()
    cutoff = (_now_utc() - timedelta(days=days)).strftime("%Y-%m-%d")
    docs = (
        db.collection("patients")
        .document(patient_id)
        .collection("daily_compliance")
        .where(filter=FieldFilter("date", ">=", cutoff))
        .order_by("date")
        .get()
    )
    return [d.to_dict() for d in docs]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def log_notification(patient_id: str, data: dict[str, Any]) -> str:
    """Log a sent notification."""
    db = _get_db()
    data.setdefault("sent_at", _now_utc())
    data.setdefault("status", "sent")
    data.setdefault("acknowledged", False)
    _, doc_ref = (
        db.collection("patients")
        .document(patient_id)
        .collection("notifications")
        .add(data)
    )
    return doc_ref.id


def get_notifications_for_date(patient_id: str, date_iso: str, rule_id: str | None = None) -> list[dict[str, Any]]:
    """Check if a notification was already sent for a rule today."""
    db = _get_db()
    query = (
        db.collection("patients")
        .document(patient_id)
        .collection("notifications")
        .where(filter=FieldFilter("date", "==", date_iso))
    )
    if rule_id:
        query = query.where(filter=FieldFilter("rule_id", "==", rule_id))
    docs = query.get()
    return [{"id": d.id, **d.to_dict()} for d in docs]


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------

def create_escalation(data: dict[str, Any]) -> str:
    """Create an escalation alert."""
    db = _get_db()
    data.setdefault("status", "open")
    data.setdefault("level", 0)
    data.setdefault("notified", [])
    data.setdefault("created_at", _now_utc())
    _, doc_ref = db.collection("escalations").add(data)
    return doc_ref.id


def get_open_escalations(patient_id: str | None = None) -> list[dict[str, Any]]:
    """Get open escalations, optionally filtered by patient."""
    db = _get_db()
    query = db.collection("escalations").where(filter=FieldFilter("status", "==", "open"))
    if patient_id:
        query = query.where(filter=FieldFilter("patient_id", "==", patient_id))
    docs = query.get()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def resolve_escalation(escalation_id: str, resolved_by: str) -> None:
    """Resolve an escalation."""
    db = _get_db()
    db.collection("escalations").document(escalation_id).update({
        "status": "resolved",
        "resolved_by": resolved_by,
        "resolved_at": _now_utc(),
    })


def update_escalation(escalation_id: str, data: dict[str, Any]) -> None:
    """Update escalation fields (e.g., bump level, add notified entry)."""
    db = _get_db()
    db.collection("escalations").document(escalation_id).update(data)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def create_provider(data: dict[str, Any]) -> str:
    """Create a provider document."""
    db = _get_db()
    _, doc_ref = db.collection("providers").add(data)
    return doc_ref.id


def get_provider_by_email(email: str) -> dict[str, Any] | None:
    """Fetch a provider by email."""
    db = _get_db()
    docs = (
        db.collection("providers")
        .where(filter=FieldFilter("email", "==", email))
        .limit(1)
        .get()
    )
    for doc in docs:
        result = doc.to_dict()
        result["id"] = doc.id
        return result
    return None


def list_patients_for_provider(provider_id: str) -> list[dict[str, Any]]:
    """List all patients assigned to a provider."""
    db = _get_db()
    provider_doc = db.collection("providers").document(provider_id).get()
    if not provider_doc.exists:
        return []
    patient_ids = provider_doc.to_dict().get("patient_ids", [])
    patients = []
    for pid in patient_ids:
        p = get_patient(pid)
        if p:
            patients.append(p)
    return patients


# ---------------------------------------------------------------------------
# Extractions  (replaces SQLite storage)
# ---------------------------------------------------------------------------

def save_extraction(extracted: dict[str, Any], simplified_summary: str) -> str:
    """Save an extraction to Firestore. Returns the document ID (string)."""
    import json

    db = _get_db()

    patient_name = ""
    if isinstance(extracted.get("patient"), dict):
        patient_name = str(extracted["patient"].get("full_name", "")).strip()

    primary_diagnosis = ""
    if isinstance(extracted.get("clinical_episode"), dict):
        primary_diagnosis = str(extracted["clinical_episode"].get("primary_diagnosis", "")).strip()

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

    data = {
        "created_at": _now_utc(),
        "source_file_name": source_file or "unknown.pdf",
        "patient_name": patient_name or None,
        "primary_diagnosis": primary_diagnosis or None,
        "followup_datetime": followup_datetime,
        "extraction_json": json.dumps(extracted, ensure_ascii=True),
        "simplified_summary": simplified_summary,
        "status": "extracted",  # extracted → registered
    }
    _, doc_ref = db.collection("extractions").add(data)
    return doc_ref.id


def get_extraction(extraction_id: str) -> dict[str, Any] | None:
    """Fetch a single extraction by document ID."""
    import json

    db = _get_db()
    doc = db.collection("extractions").document(extraction_id).get()
    if not doc.exists:
        return None
    payload = doc.to_dict()
    payload["id"] = doc.id
    # Deserialize extraction_json string → dict
    raw = payload.get("extraction_json")
    if isinstance(raw, str):
        try:
            payload["extraction_json"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return payload


def list_extractions(limit: int = 100) -> list[dict[str, Any]]:
    """List recent extractions (lightweight — no extraction_json blob)."""
    db = _get_db()
    docs = (
        db.collection("extractions")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(max(1, min(limit, 500)))
        .get()
    )
    results = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        # Convert Firestore Timestamp to ISO string for JSON serialization
        created = d.get("created_at")
        if hasattr(created, "isoformat"):
            d["created_at"] = created.isoformat()
        # Strip the heavy blob from listing
        d.pop("extraction_json", None)
        d.pop("simplified_summary", None)
        results.append(d)
    return results


def update_extraction(extraction_id: str, data: dict[str, Any]) -> None:
    """Update extraction fields (e.g. status → 'registered')."""
    db = _get_db()
    data["updated_at"] = _now_utc()
    db.collection("extractions").document(extraction_id).update(data)


# ---------------------------------------------------------------------------
# Provider Notes  (clinical documentation per patient)
# ---------------------------------------------------------------------------

def save_provider_note(patient_id: str, data: dict[str, Any]) -> str:
    """Save a provider clinical note for a patient."""
    db = _get_db()
    data.setdefault("created_at", _now_utc())
    data.setdefault("author", "nurse")
    _, doc_ref = (
        db.collection("patients")
        .document(patient_id)
        .collection("provider_notes")
        .add(data)
    )
    return doc_ref.id


def get_provider_notes(patient_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """Get recent clinical notes for a patient, newest first."""
    db = _get_db()
    docs = (
        db.collection("patients")
        .document(patient_id)
        .collection("provider_notes")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(max(1, min(limit, 200)))
        .get()
    )
    results = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        created = d.get("created_at")
        if hasattr(created, "isoformat"):
            d["created_at"] = created.isoformat()
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Enhanced Escalation Resolution
# ---------------------------------------------------------------------------

def resolve_escalation_with_details(
    escalation_id: str,
    resolution_type: str,
    action_taken: str | None = None,
    note: str | None = None,
    resolved_by: str = "nurse",
) -> None:
    """Resolve an escalation with clinical context."""
    db = _get_db()
    data: dict[str, Any] = {
        "status": "resolved",
        "resolution_type": resolution_type,
        "resolved_by": resolved_by,
        "resolved_at": _now_utc(),
    }
    if action_taken:
        data["action_taken"] = action_taken
    if note:
        data["resolution_note"] = note
    db.collection("escalations").document(escalation_id).update(data)


def get_all_escalations(
    patient_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get both open and resolved escalations, newest first."""
    db = _get_db()
    query = db.collection("escalations").order_by("created_at", direction=firestore.Query.DESCENDING)
    if patient_id:
        query = query.where(filter=FieldFilter("patient_id", "==", patient_id))
    docs = query.limit(max(1, min(limit, 200))).get()
    results = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        # Serialize timestamps
        for ts_field in ("created_at", "resolved_at"):
            val = d.get(ts_field)
            if hasattr(val, "isoformat"):
                d[ts_field] = val.isoformat()
        results.append(d)
    return results
