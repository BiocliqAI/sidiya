"""Escalation engine for Sidiya Reminder System.

Detects missed patient actions (weight, medications) and vital threshold
breaches. Manages the escalation lifecycle: patient → caregiver → nurse.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app import firestore_client as fdb
from app.notifications import send_notification

logger = logging.getLogger(__name__)


def _ist_now() -> datetime:
    """Current time in IST (UTC+5:30)."""
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


# ---------------------------------------------------------------------------
# Threshold checks (called immediately when patient logs a vital)
# ---------------------------------------------------------------------------

def check_weight_thresholds(patient_id: str, new_weight: float) -> dict[str, Any] | None:
    """Check if a newly logged weight triggers an escalation.

    Returns escalation data if triggered, None otherwise.
    """
    patient = fdb.get_patient(patient_id)
    if not patient:
        return None

    thresholds = patient.get("thresholds", {})
    trigger_24h = thresholds.get("weight_gain_trigger_24h_kg", 1.0)
    trigger_7d = thresholds.get("weight_gain_trigger_7d_kg", 2.0)

    # Get yesterday's weight
    yesterday = (_ist_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_logs = fdb.get_vitals_for_date(patient_id, yesterday, "weight")
    if yesterday_logs:
        yesterday_weight = yesterday_logs[-1].get("value")
        if isinstance(yesterday_weight, (int, float)):
            gain_24h = new_weight - yesterday_weight
            if gain_24h >= trigger_24h:
                return _create_weight_escalation(
                    patient, "weight_spike_24h",
                    f"Weight gain of {gain_24h:.1f}kg in 24 hours (threshold: {trigger_24h}kg)",
                    new_weight, trigger_24h,
                )

    # Get 7-day-ago weight
    seven_days_ago = (_ist_now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_logs = fdb.get_vitals_for_date(patient_id, seven_days_ago, "weight")
    if week_logs:
        week_weight = week_logs[0].get("value")
        if isinstance(week_weight, (int, float)):
            gain_7d = new_weight - week_weight
            if gain_7d >= trigger_7d:
                return _create_weight_escalation(
                    patient, "weight_spike_7d",
                    f"Weight gain of {gain_7d:.1f}kg in 7 days (threshold: {trigger_7d}kg)",
                    new_weight, trigger_7d,
                )

    return None


def check_symptom_red_flags(patient_id: str, symptoms: list[str]) -> dict[str, Any] | None:
    """Check if reported symptoms match red zone flags. Immediately escalate to nurse."""
    patient = fdb.get_patient(patient_id)
    if not patient:
        return None

    thresholds = patient.get("thresholds", {})
    red_zone = [s.lower() for s in thresholds.get("red_zone", [])]

    reported_lower = [s.lower() for s in symptoms]
    matches = [s for s in reported_lower if any(rz in s or s in rz for rz in red_zone)]

    if matches:
        esc_data = {
            "patient_id": patient_id,
            "trigger_type": "red_flag",
            "trigger_value": ", ".join(matches),
            "threshold": "red zone symptoms",
            "level": 2,  # Skip straight to nurse
        }
        esc_id = fdb.create_escalation(esc_data)

        # Immediately notify nurse
        _notify_nurse(patient, f"RED FLAG: {patient.get('full_name', 'Patient')} reported: {', '.join(matches)}. Immediate attention required.")

        # Also notify caregiver
        _notify_caregiver(patient, f"Alert: {patient.get('full_name', 'Patient')} reported concerning symptoms: {', '.join(matches)}. Please check on them.")

        return {"escalation_id": esc_id, "matched_symptoms": matches}

    return None


def _create_weight_escalation(patient: dict, trigger_type: str, message: str, value: float, threshold: float) -> dict[str, Any]:
    """Create a weight-spike escalation and immediately notify nurse."""
    esc_data = {
        "patient_id": patient["id"],
        "trigger_type": trigger_type,
        "trigger_value": value,
        "threshold": threshold,
        "level": 2,  # Weight spikes go straight to nurse
    }
    esc_id = fdb.create_escalation(esc_data)

    _notify_nurse(patient, f"WEIGHT ALERT: {patient.get('full_name', 'Patient')} — {message}")
    _notify_caregiver(patient, f"Weight alert for {patient.get('full_name', 'Patient')}: {message}. Please ensure they contact their care team.")

    return {"escalation_id": esc_id, "message": message}


# ---------------------------------------------------------------------------
# Missed action detection (called by cron every 30 minutes)
# ---------------------------------------------------------------------------

def check_missed_actions() -> dict[str, int]:
    """Evaluate all active patients for missed actions.

    Called by Cloud Scheduler every 30 minutes.
    """
    now = _ist_now()
    current_time = now.strftime("%H:%M")
    today_iso = now.strftime("%Y-%m-%d")

    stats = {"checked": 0, "new_escalations": 0, "level_ups": 0}

    patients = fdb.list_active_patients()
    for patient in patients:
        patient_id = patient["id"]
        stats["checked"] += 1

        # Check missed weight (after 12:00 IST)
        if current_time >= "12:00":
            weight_logs = fdb.get_vitals_for_date(patient_id, today_iso, "weight")
            if not weight_logs:
                _handle_missed_action(patient, "missed_weight", today_iso, current_time, stats)

        # Check missed medications
        rules = fdb.get_reminder_rules(patient_id)
        for rule in rules:
            if rule.get("type") != "medication":
                continue
            schedule = rule.get("schedule", {})
            for scheduled_time in schedule.get("times", []):
                # Check if past the escalation window
                esc_config = rule.get("escalation", {})
                after_minutes = esc_config.get("after_minutes", 60) if esc_config else 60

                try:
                    h, m = map(int, scheduled_time.split(":"))
                    due_minutes = h * 60 + m + after_minutes
                    current_minutes = int(now.strftime("%H")) * 60 + int(now.strftime("%M"))
                    if current_minutes < due_minutes:
                        continue
                except (ValueError, TypeError):
                    continue

                # Check if medication was logged
                med_name = rule.get("payload", {}).get("medication_name", "")
                med_logs = fdb.get_medication_logs_for_date(patient_id, today_iso)
                logged = any(
                    ml.get("medication_name") == med_name and ml.get("scheduled_time") == scheduled_time
                    for ml in med_logs
                )
                if not logged:
                    _handle_missed_action(
                        patient, "missed_medication", today_iso, current_time, stats,
                        extra={"medication_name": med_name, "scheduled_time": scheduled_time},
                    )

        # Check for consecutive missed weight days (3+ days → immediate nurse escalation)
        missed_days = 0
        for days_ago in range(1, 4):
            past_date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            past_logs = fdb.get_vitals_for_date(patient_id, past_date, "weight")
            if not past_logs:
                missed_days += 1
            else:
                break

        if missed_days >= 3:
            existing = fdb.get_open_escalations(patient_id)
            has_consec = any(e.get("trigger_type") == "consecutive_missed_weight" for e in existing)
            if not has_consec:
                esc_data = {
                    "patient_id": patient_id,
                    "trigger_type": "consecutive_missed_weight",
                    "trigger_value": missed_days,
                    "threshold": 3,
                    "level": 2,
                }
                fdb.create_escalation(esc_data)
                _notify_nurse(
                    patient,
                    f"ALERT: {patient.get('full_name', 'Patient')} has not logged weight for {missed_days} consecutive days.",
                )
                stats["new_escalations"] += 1

    logger.info("Missed action check complete: %s", stats)
    return stats


def _handle_missed_action(
    patient: dict,
    trigger_type: str,
    today_iso: str,
    current_time: str,
    stats: dict[str, int],
    extra: dict | None = None,
) -> None:
    """Handle a single missed action: create or escalate."""
    patient_id = patient["id"]

    # Check if escalation already exists for this trigger today
    open_escs = fdb.get_open_escalations(patient_id)
    existing = None
    for esc in open_escs:
        if esc.get("trigger_type") == trigger_type and esc.get("date", "") == today_iso:
            if extra:
                # For medications, match by specific med name
                if esc.get("payload", {}).get("medication_name") == extra.get("medication_name"):
                    existing = esc
                    break
            else:
                existing = esc
                break

    if existing:
        # Check if we should bump the escalation level
        level = existing.get("level", 0)
        created_at = existing.get("created_at")
        if created_at:
            if hasattr(created_at, "timestamp"):
                age_minutes = (_ist_now().timestamp() - created_at.timestamp()) / 60
            else:
                age_minutes = 0

            if level == 0 and age_minutes >= 120:
                # Bump to level 1: notify caregiver
                fdb.update_escalation(existing["id"], {"level": 1})
                _notify_caregiver(
                    patient,
                    f"{patient.get('full_name', 'Patient')} hasn't {_action_verb(trigger_type)}. Please remind them.",
                )
                stats["level_ups"] += 1
            elif level == 1 and age_minutes >= 240:
                # Bump to level 2: notify nurse
                fdb.update_escalation(existing["id"], {"level": 2})
                _notify_nurse(
                    patient,
                    f"{patient.get('full_name', 'Patient')} — {_action_description(trigger_type)}. No response from caregiver.",
                )
                stats["level_ups"] += 1
    else:
        # Create new escalation at level 0 (patient gets re-reminded)
        esc_data = {
            "patient_id": patient_id,
            "trigger_type": trigger_type,
            "date": today_iso,
            "level": 0,
            "payload": extra or {},
        }
        fdb.create_escalation(esc_data)

        # Re-remind the patient
        body = _patient_reminder_text(trigger_type, extra)
        send_notification(patient, "Reminder", body, trigger_type)
        stats["new_escalations"] += 1


# ---------------------------------------------------------------------------
# Auto-resolve escalations when patient acts
# ---------------------------------------------------------------------------

def resolve_escalations_for_action(patient_id: str, action_type: str, extra: dict | None = None) -> int:
    """Resolve open escalations when a patient takes the expected action.

    Returns the number of resolved escalations.
    """
    trigger_map = {
        "weight": ["missed_weight", "consecutive_missed_weight"],
        "medication": ["missed_medication"],
        "symptom_check": ["missed_symptom_check"],
    }
    trigger_types = trigger_map.get(action_type, [])
    if not trigger_types:
        return 0

    open_escs = fdb.get_open_escalations(patient_id)
    resolved = 0
    for esc in open_escs:
        if esc.get("trigger_type") in trigger_types:
            if action_type == "medication" and extra:
                # Only resolve if same medication
                if esc.get("payload", {}).get("medication_name") != extra.get("medication_name"):
                    continue
            fdb.resolve_escalation(esc["id"], "patient_action")
            resolved += 1

    return resolved


# ---------------------------------------------------------------------------
# Helper: notify caregiver / nurse
# ---------------------------------------------------------------------------

def _notify_caregiver(patient: dict, message: str) -> None:
    """Send notification to the patient's caregiver."""
    caregiver_phone = patient.get("caregiver_phone")
    if not caregiver_phone:
        logger.warning("No caregiver phone for patient %s", patient.get("id"))
        return

    from app.notifications import _send_sms
    _send_sms(caregiver_phone, f"Sidiya Alert: {message}")


def _notify_nurse(patient: dict, message: str) -> None:
    """Send notification to the assigned nurse/provider."""
    nurse_phone = patient.get("nurse_phone")
    if not nurse_phone:
        logger.warning("No nurse phone for patient %s", patient.get("id"))
        return

    from app.notifications import _send_sms
    _send_sms(nurse_phone, f"Sidiya Provider Alert: {message}")


def _action_verb(trigger_type: str) -> str:
    verbs = {
        "missed_weight": "logged their weight today",
        "missed_medication": "taken their medication",
        "missed_symptom_check": "completed their symptom check",
    }
    return verbs.get(trigger_type, "completed a required action")


def _action_description(trigger_type: str) -> str:
    descriptions = {
        "missed_weight": "Missed weight log today",
        "missed_medication": "Missed medication dose",
        "missed_symptom_check": "Missed evening symptom check",
    }
    return descriptions.get(trigger_type, "Missed required action")


def _patient_reminder_text(trigger_type: str, extra: dict | None = None) -> str:
    if trigger_type == "missed_weight":
        return "You haven't logged your weight yet today. Please weigh yourself and log it in the app."
    if trigger_type == "missed_medication":
        med_name = (extra or {}).get("medication_name", "your medication")
        return f"Reminder: You haven't taken {med_name} yet. Please take it now if appropriate."
    if trigger_type == "missed_symptom_check":
        return "Please complete your evening symptom check-in."
    return "You have a pending health task. Please check the Sidiya app."
