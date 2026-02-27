"""Reminder Engine for Sidiya.

Parses extraction JSON into concrete reminder rules stored in Firestore.
Handles medication frequency notation (Indian "1-0-1" format),
monitoring schedules, appointment reminders, and nurse check-ins.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from app import firestore_client as fdb

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Medication frequency → scheduled times mapping
# ---------------------------------------------------------------------------

# Indian discharge summaries use notation like "1-0-1" meaning morning-afternoon-night
# where 1 = take, 0 = skip. Also handles text frequencies.
_FREQ_TIME_MAP: dict[str, list[str]] = {
    # Pattern-based (1-0-1, 1-1-1, etc.)
    "1-0-0": ["08:00"],
    "0-1-0": ["14:00"],
    "0-0-1": ["21:00"],
    "1-1-0": ["08:00", "14:00"],
    "1-0-1": ["08:00", "21:00"],
    "0-1-1": ["14:00", "21:00"],
    "1-1-1": ["08:00", "14:00", "21:00"],
    "1-1-1-1": ["06:00", "12:00", "18:00", "22:00"],
    # Text-based
    "once daily": ["08:00"],
    "od": ["08:00"],
    "once a day": ["08:00"],
    "qd": ["08:00"],
    "daily": ["08:00"],
    "bd": ["08:00", "21:00"],
    "bid": ["08:00", "21:00"],
    "twice daily": ["08:00", "21:00"],
    "twice a day": ["08:00", "21:00"],
    "tds": ["08:00", "14:00", "21:00"],
    "tid": ["08:00", "14:00", "21:00"],
    "thrice daily": ["08:00", "14:00", "21:00"],
    "three times a day": ["08:00", "14:00", "21:00"],
    "qid": ["06:00", "12:00", "18:00", "22:00"],
    "four times a day": ["06:00", "12:00", "18:00", "22:00"],
    "at night": ["21:00"],
    "hs": ["21:00"],
    "at bedtime": ["21:00"],
    "morning": ["08:00"],
    "evening": ["18:00"],
    "night": ["21:00"],
    "sos": [],  # as needed — no scheduled reminder
    "prn": [],  # as needed
    "stat": [],  # one-time
    "weekly": ["08:00"],  # weekly meds get a single daily time
}


def _parse_frequency_to_times(frequency: str) -> list[str]:
    """Convert a medication frequency string into a list of scheduled times."""
    freq = (frequency or "").strip().lower()

    # Try direct match
    if freq in _FREQ_TIME_MAP:
        return _FREQ_TIME_MAP[freq]

    # Try pattern match: look for "1-0-1" style inside the string
    pattern_match = re.search(r"\b([01]-[01]-[01](?:-[01])?)\b", freq)
    if pattern_match:
        pattern = pattern_match.group(1)
        if pattern in _FREQ_TIME_MAP:
            return _FREQ_TIME_MAP[pattern]

    # Keyword search in the string
    freq_lower = freq.lower()
    for key, times in _FREQ_TIME_MAP.items():
        if key in freq_lower and times:
            return times

    # Default: once daily morning if we can't parse
    if freq and freq != "unknown":
        logger.warning("Could not parse medication frequency '%s', defaulting to once daily", frequency)
        return ["08:00"]

    return ["08:00"]


def _is_unknown(value: Any) -> bool:
    if not value:
        return True
    return str(value).strip().lower() in ("unknown", "n/a", "none", "")


# ---------------------------------------------------------------------------
# Generate reminder rules from extraction
# ---------------------------------------------------------------------------

def generate_reminder_rules(patient_id: str, extraction: dict[str, Any]) -> dict[str, int]:
    """Parse extraction JSON and create reminder rules for a patient.

    Returns a summary dict with counts of created rules per type.
    """
    counts: dict[str, int] = {}

    # 1. Medication reminders
    meds = extraction.get("medications", {}).get("discharge_medications", [])
    for med in meds:
        if not isinstance(med, dict):
            continue
        name = med.get("medication_name", "unknown")
        if _is_unknown(name):
            continue

        times = _parse_frequency_to_times(med.get("frequency", ""))
        if not times:
            continue  # SOS/PRN meds — no scheduled reminder

        rule = {
            "type": "medication",
            "schedule": {"times": times, "days": "daily"},
            "payload": {
                "medication_name": name,
                "dose": med.get("dose", ""),
                "route": med.get("route", ""),
                "frequency": med.get("frequency", ""),
                "indication": med.get("indication", ""),
            },
            "phase": "all",
            "escalation": {"after_minutes": 60, "notify": ["caregiver"]},
        }
        fdb.add_reminder_rule(patient_id, rule)
        counts["medication"] = counts.get("medication", 0) + 1

    # 2. Daily weight monitoring
    monitoring = (
        extraction.get("clinical_modules", {})
        .get("chf", {})
        .get("monitoring", {})
    )
    if monitoring.get("daily_weight_required", True):
        rule = {
            "type": "weight",
            "schedule": {"times": ["07:30"], "days": "daily"},
            "payload": {
                "message": "Time to log your weight. Please weigh yourself before eating or drinking.",
                "target_weight_kg": extraction.get("clinical_modules", {}).get("chf", {}).get("congestion_status", {}).get("target_dry_weight_kg"),
            },
            "phase": "all",
            "escalation": {"after_minutes": 270, "notify": ["caregiver", "nurse"]},  # 4.5 hours → noon
        }
        fdb.add_reminder_rule(patient_id, rule)
        counts["weight"] = 1

    # 3. Blood pressure monitoring
    if monitoring.get("bp_required", True):
        rule = {
            "type": "bp",
            "schedule": {"times": ["08:30"], "days": "daily"},
            "payload": {"message": "Please log your blood pressure reading."},
            "phase": "all",
            "escalation": {"after_minutes": 480, "notify": ["caregiver"]},
        }
        fdb.add_reminder_rule(patient_id, rule)
        counts["bp"] = 1

    # 4. Symptom check-in (evening)
    if monitoring.get("symptom_check_required", True):
        rule = {
            "type": "symptom_check",
            "schedule": {"times": ["19:00"], "days": "daily"},
            "payload": {"message": "Evening check-in: How are you feeling today?"},
            "phase": "all",
            "escalation": {"after_minutes": 180, "notify": ["caregiver"]},
        }
        fdb.add_reminder_rule(patient_id, rule)
        counts["symptom_check"] = 1

    # 5. Appointment reminders
    appointments = extraction.get("follow_up", {}).get("appointments", [])
    for appt in appointments:
        if not isinstance(appt, dict):
            continue
        scheduled_dt = appt.get("scheduled_datetime")
        if not scheduled_dt:
            continue

        try:
            appt_date = datetime.fromisoformat(scheduled_dt)
        except (ValueError, TypeError):
            continue

        provider = appt.get("provider_name", "your doctor")
        appt_type = appt.get("appointment_type", "follow-up")

        # Reminder 2 days before
        reminder_2d = appt_date - timedelta(days=2)
        rule = {
            "type": "appointment",
            "schedule": {
                "times": ["09:00"],
                "days": [reminder_2d.strftime("%Y-%m-%d")],
            },
            "payload": {
                "message": f"Reminder: Your {appt_type} appointment with {provider} is in 2 days.",
                "appointment_datetime": scheduled_dt,
                "provider": provider,
            },
            "phase": "all",
            "escalation": None,
        }
        fdb.add_reminder_rule(patient_id, rule)

        # Reminder 1 day before
        reminder_1d = appt_date - timedelta(days=1)
        rule_1d = {
            "type": "appointment",
            "schedule": {
                "times": ["09:00"],
                "days": [reminder_1d.strftime("%Y-%m-%d")],
            },
            "payload": {
                "message": f"Reminder: Your {appt_type} appointment with {provider} is tomorrow.",
                "appointment_datetime": scheduled_dt,
                "provider": provider,
            },
            "phase": "all",
            "escalation": None,
        }
        fdb.add_reminder_rule(patient_id, rule_1d)

        # Same-day reminder
        rule_0d = {
            "type": "appointment",
            "schedule": {
                "times": ["07:00"],
                "days": [appt_date.strftime("%Y-%m-%d")],
            },
            "payload": {
                "message": f"Today: {appt_type} appointment with {provider}. Please be on time.",
                "appointment_datetime": scheduled_dt,
                "provider": provider,
            },
            "phase": "all",
            "escalation": None,
        }
        fdb.add_reminder_rule(patient_id, rule_0d)
        counts["appointment"] = counts.get("appointment", 0) + 3

    # 6. Nurse check-in reminders (days 0, 2, 6, then weekly)
    care_start = extraction.get("care_plan_90d", {}).get("start_date")
    if care_start:
        try:
            start_date = datetime.fromisoformat(care_start)
        except (ValueError, TypeError):
            start_date = None

        if start_date:
            checkin_days = [0, 2, 6] + list(range(13, 91, 7))  # day 0,2,6, then every 7 days from day 13
            checkin_dates = [(start_date + timedelta(days=d)).strftime("%Y-%m-%d") for d in checkin_days]
            rule = {
                "type": "nurse_checkin",
                "schedule": {"times": ["10:00"], "days": checkin_dates},
                "payload": {"message": "Nurse check-in and symptom review scheduled for today."},
                "phase": "all",
                "escalation": None,
                "target": "nurse",  # This reminder goes to the nurse, not the patient
            }
            fdb.add_reminder_rule(patient_id, rule)
            counts["nurse_checkin"] = 1

    # 7. Store red flag thresholds on the patient document for escalation checks
    red_flags = (
        extraction.get("clinical_modules", {})
        .get("chf", {})
        .get("red_flags", {})
    )
    fdb.update_patient(patient_id, {
        "thresholds": {
            "weight_gain_trigger_24h_kg": red_flags.get("weight_gain_trigger_24h_kg", 1.0),
            "weight_gain_trigger_7d_kg": red_flags.get("weight_gain_trigger_7d_kg", 2.0),
            "yellow_zone": red_flags.get("yellow_zone", []),
            "red_zone": red_flags.get("red_zone", []),
        },
    })

    logger.info("Generated reminder rules for patient %s: %s", patient_id, counts)
    return counts


# ---------------------------------------------------------------------------
# Compute daily compliance
# ---------------------------------------------------------------------------

def compute_daily_compliance(patient_id: str, date_iso: str, extraction: dict[str, Any]) -> dict[str, Any]:
    """Compute and store the daily compliance score for a patient."""
    # Count expected medications
    meds = extraction.get("medications", {}).get("discharge_medications", [])
    expected_med_count = 0
    for med in meds:
        if not isinstance(med, dict):
            continue
        times = _parse_frequency_to_times(med.get("frequency", ""))
        expected_med_count += len(times)

    # Count logged medications
    med_logs = fdb.get_medication_logs_for_date(patient_id, date_iso)
    taken_count = sum(1 for m in med_logs if m.get("status") == "taken")
    skipped_count = sum(1 for m in med_logs if m.get("status") == "skipped")

    # Check vital logs
    weight_logs = fdb.get_vitals_for_date(patient_id, date_iso, "weight")
    bp_logs = fdb.get_vitals_for_date(patient_id, date_iso, "bp")
    symptom_logs = fdb.get_vitals_for_date(patient_id, date_iso, "symptom_check")

    weight_logged = len(weight_logs) > 0
    bp_logged = len(bp_logs) > 0
    symptom_check_done = len(symptom_logs) > 0

    # Calculate compliance score
    expected_actions = expected_med_count + 3  # +3 for weight, BP, symptom check
    completed_actions = taken_count + (1 if weight_logged else 0) + (1 if bp_logged else 0) + (1 if symptom_check_done else 0)
    score = round(completed_actions / max(expected_actions, 1), 2)

    # Determine care plan day and phase
    care_start = extraction.get("care_plan_90d", {}).get("start_date")
    care_plan_day = 0
    phase = "0-7"
    if care_start:
        try:
            start = datetime.fromisoformat(care_start).date()
            current = datetime.fromisoformat(date_iso).date()
            care_plan_day = (current - start).days
            if care_plan_day <= 7:
                phase = "0-7"
            elif care_plan_day <= 30:
                phase = "8-30"
            else:
                phase = "31-90"
        except (ValueError, TypeError):
            pass

    compliance = {
        "date": date_iso,
        "care_plan_day": care_plan_day,
        "phase": phase,
        "weight_logged": weight_logged,
        "bp_logged": bp_logged,
        "symptom_check_done": symptom_check_done,
        "medications_expected": expected_med_count,
        "medications_taken": taken_count,
        "medications_skipped": skipped_count,
        "compliance_score": score,
    }
    fdb.update_daily_compliance(patient_id, date_iso, compliance)
    return compliance
