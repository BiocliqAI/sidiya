from __future__ import annotations

from typing import Any


def build_simplified_summary(extracted: dict[str, Any]) -> str:
    patient = extracted.get("patient", {}) if isinstance(extracted.get("patient"), dict) else {}
    clinical = extracted.get("clinical_episode", {}) if isinstance(extracted.get("clinical_episode"), dict) else {}
    encounter = extracted.get("encounter", {}) if isinstance(extracted.get("encounter"), dict) else {}
    follow = extracted.get("follow_up", {}) if isinstance(extracted.get("follow_up"), dict) else {}
    meds = extracted.get("medications", {}) if isinstance(extracted.get("medications"), dict) else {}
    details = extracted.get("extracted_details", {}) if isinstance(extracted.get("extracted_details"), dict) else {}
    advice = details.get("discharge_advice", {}) if isinstance(details.get("discharge_advice"), dict) else {}

    appointments = follow.get("appointments", []) if isinstance(follow.get("appointments"), list) else []
    first_appt = appointments[0] if appointments and isinstance(appointments[0], dict) else {}
    med_rows = meds.get("discharge_medications", []) if isinstance(meds.get("discharge_medications"), list) else []
    med_count = len(med_rows)

    patient_name = patient.get("full_name") or "Patient"
    dx = clinical.get("primary_diagnosis") or "Not available"
    reason = clinical.get("reason_for_hospitalization") or "Not available"
    discharge_date = encounter.get("discharge_datetime") or "Not available"
    follow_dt = first_appt.get("scheduled_datetime") or "Not scheduled"
    follow_doc = first_appt.get("provider_name") or "Not assigned"
    diet = advice.get("diet") or "Follow discharge diet instructions"
    fluid = advice.get("fluid") or "Follow fluid instructions"
    activity = advice.get("activity") or "Follow activity instructions"

    lines = [
        f"Patient: {patient_name}",
        f"Primary diagnosis: {dx}",
        f"Reason for admission: {reason}",
        f"Discharge date/time: {discharge_date}",
        f"Medication count: {med_count}",
        f"Follow-up: {follow_dt} with {follow_doc}",
        "Home-care priorities:",
        f"- Diet: {diet}",
        f"- Fluid: {fluid}",
        f"- Activity: {activity}",
        "- Take medications exactly as prescribed and do not skip doses.",
        "- Monitor symptoms daily and seek urgent care for severe breathlessness, chest pain, fainting, or confusion.",
    ]
    return "\n".join(lines)

