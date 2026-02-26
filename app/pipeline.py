from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import ValidationError, validate

from app.config import settings
from app.gemini_client import GeminiClient, GeminiError
from app.landing_client import LandingClient, LandingError

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "discharge_chf_output.schema.json"

EXTRACTION_SYSTEM_PROMPT = """
You are a clinical document extractor creating a structured care-plan JSON.
Requirements:
- Output STRICT JSON only.
- Scope: adult post-surgical or post-critical non-surgical discharge.
- Exclude childbirth/postpartum from care logic.
- Preserve dates and times exactly when known.
- Do not emit "unknown" for fields explicitly present in the parsed markdown.
""".strip()

MEDICATION_INDICATION_SYSTEM_PROMPT = """
You map discharge medications to patient-friendly clinical purpose.
Return STRICT JSON only.
Do not change medication names.
Prefer concrete purposes such as: antiplatelet, anticoagulation, heart-failure therapy,
diuretic for fluid removal, BP control, lipid lowering, diabetes control, gastric protection,
pain control, antiarrhythmic.
If uncertain but inferable from drug class/name, provide "likely <purpose>" instead of "unknown".
""".strip()

_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")


def _landing_to_ocr_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    metadata = parsed.get("metadata", {}) if isinstance(parsed.get("metadata"), dict) else {}
    extraction_status = parsed.get("extraction_status", {}) if isinstance(parsed.get("extraction_status"), dict) else {}
    page_count = int(metadata.get("num_pages") or metadata.get("page_count") or 1)
    failed_pages = extraction_status.get("failed_pages", [])
    failed_count = len(failed_pages) if isinstance(failed_pages, list) else 0

    if page_count > 0:
        ocr_quality_score = max(0.0, min(1.0, 1.0 - (failed_count / page_count)))
    else:
        ocr_quality_score = 0.5

    return {
        "page_count": page_count,
        "ocr_quality_score": ocr_quality_score,
        "illegible_sections": [f"page_{p}" for p in failed_pages] if isinstance(failed_pages, list) else [],
        "markdown": parsed.get("markdown", ""),
    }


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return default


def _is_unknown(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "unknown", "na", "n/a", "not available", "none", "nil"}
    return False


def _first_known(*values: Any, default: Any = None) -> Any:
    for value in values:
        if not _is_unknown(value):
            return value
    return default


def _clean_text(text: str) -> str:
    if not text:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", text)
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def _parse_date(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_datetime(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d/%m/%y",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt in ("%d/%m/%Y", "%d/%m/%y"):
                dt = dt.replace(hour=12, minute=0, second=0)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def _extract_first_date(text: str) -> str | None:
    match = _DATE_RE.search(text)
    return match.group(1) if match else None


def _normalize_route(route_text: str) -> str:
    upper = route_text.upper()
    if "ORAL" in upper:
        return "ORAL"
    if "IV" in upper:
        return "IV"
    if "IM" in upper:
        return "IM"
    if "SC" in upper or "SUBCUT" in upper:
        return "SC"
    if "INHAL" in upper or "NEB" in upper:
        return "INHALATION"
    if "TOPICAL" in upper:
        return "TOPICAL"
    return route_text or "unknown"


def _extract_medications_from_markdown(markdown: str) -> list[dict[str, str]]:
    meds: list[dict[str, str]] = []
    row_pattern = re.compile(
        r"<tr><td[^>]*>\s*\d+\s*</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td>(?:<td[^>]*>.*?</td>){0,3}</tr>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in row_pattern.finditer(markdown):
        name = _clean_text(match.group(1))
        dose = _clean_text(match.group(2))
        frequency = _clean_text(match.group(3))
        route_raw = _clean_text(match.group(4))

        upper_name = name.upper()
        if not any(token in upper_name for token in ("TAB", "CAP", "INJ", "SYP", "SYRUP", "DROP", "OINT")):
            continue

        meds.append(
            {
                "medication_name": name or "unknown",
                "dose": dose or "unknown",
                "route": _normalize_route(route_raw),
                "frequency": frequency or "unknown",
                "indication": "unknown",
            }
        )

    return meds


def _extract_markdown_heuristics(markdown: str) -> dict[str, Any]:
    md = markdown or ""
    heuristics: dict[str, Any] = {
        "patient": {},
        "encounter": {},
        "clinical_episode": {},
        "follow_up": {},
        "advice": {},
        "emergency_signs": [],
        "medications": [],
    }

    if "THE MADRAS MEDICAL MISSION" in md.upper():
        heuristics["encounter"]["facility_name"] = "THE MADRAS MEDICAL MISSION"

    mrn_match = re.search(r"\bUHID\s*[:\-]\s*([A-Z0-9]+)", md, flags=re.IGNORECASE)
    if mrn_match:
        heuristics["patient"]["mrn"] = mrn_match.group(1).strip()

    name_match = re.search(r"\bNAME\s*[:\-]\s*([A-Z][A-Z\.\s]+)", md, flags=re.IGNORECASE)
    if name_match:
        heuristics["patient"]["full_name"] = _clean_text(name_match.group(1))

    ip_match = re.search(r"\bIP\s*NO\.?\s*[:\-]\s*([A-Z0-9]+)", md, flags=re.IGNORECASE)
    if ip_match:
        heuristics["patient"]["ip_no"] = ip_match.group(1).strip()

    phone_match = re.search(r"\bPHONE\s*NO\.?\s*[:\-]\s*([0-9+\-\s]{8,20})", md, flags=re.IGNORECASE)
    if phone_match:
        heuristics["patient"]["phone"] = _clean_text(phone_match.group(1))

    address_match = re.search(r"\bADDRESS\s*[:\-]\s*(.+?)\s*(?:</td>|\\n<tr>|\\n<a id=)", md, flags=re.IGNORECASE | re.DOTALL)
    if address_match:
        heuristics["patient"]["address"] = _clean_text(address_match.group(1))

    sex_match = re.search(r"\((\d+)\s*Years\s*/\s*([MF])\)", md, flags=re.IGNORECASE)
    if sex_match:
        heuristics["patient"]["sex_at_birth"] = "male" if sex_match.group(2).upper() == "M" else "female"
        heuristics["patient"]["age_years"] = int(sex_match.group(1))

    doa_match = re.search(
        r"\bDOA\b\s*:?\s*(?:</td>\s*<td[^>]*>)?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        md,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if doa_match:
        heuristics["encounter"]["admission_date"] = doa_match.group(1)

    dod_match = re.search(
        r"\bDOD\b\s*:?\s*(?:</td>\s*<td[^>]*>)?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        md,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if dod_match:
        heuristics["encounter"]["discharge_date"] = dod_match.group(1)

    diag_block_match = re.search(
        r"\*\*DIAGNOSIS\s*:\*\*(.*?)(?:\n<a id=|<!-- PAGE BREAK -->|BRIEF HISTORY|COURSE IN THE HOSPITAL)",
        md,
        flags=re.IGNORECASE | re.DOTALL,
    )
    diagnosis_items: list[str] = []
    if diag_block_match:
        diagnosis_items = [_clean_text(x) for x in re.findall(r"^\s*\*\s*(.+)$", diag_block_match.group(1), flags=re.MULTILINE)]
        diagnosis_items = [x for x in diagnosis_items if x]

    if diagnosis_items:
        heuristics["clinical_episode"]["diagnoses"] = diagnosis_items
        heuristics["clinical_episode"]["primary_diagnosis"] = diagnosis_items[0]
        lvef_diag_match = re.search(r"EF\s*[-:=]?\s*(\d{1,2}(?:\.\d+)?)\s*%", " ".join(diagnosis_items), flags=re.IGNORECASE)
        if lvef_diag_match:
            heuristics["clinical_episode"]["lvef_percent"] = float(lvef_diag_match.group(1))

    brief_history_match = re.search(
        r"BRIEF HISTORY\s*:\s*(.*?)(?:\n<a id=|O/E:|S/E|INVESTIGATIONS|COURSE IN THE HOSPITAL)",
        md,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if brief_history_match:
        heuristics["clinical_episode"]["reason_for_hospitalization"] = _clean_text(brief_history_match.group(1))

    course_match = re.search(
        r"COURSE IN THE HOSPITAL\s*:\s*(.*?)(?:\n<a id=|<table id=\"5-1\"|PRESCRIPTION DETAILS|ADVICE ON DISCHARGE)",
        md,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if course_match:
        heuristics["clinical_episode"]["hospital_course_summary"] = _clean_text(course_match.group(1))

    past_history_match = re.search(
        r"NAME:\s*[A-Z\.\s]+</a>\s*(.*?)\s*<a id='[^']+'></a>\s*PLAN\s*:",
        md,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if past_history_match:
        history_lines = [_clean_text(x) for x in past_history_match.group(1).splitlines() if _clean_text(x)]
        if history_lines:
            heuristics["clinical_episode"]["past_history"] = history_lines

    echo_match = re.search(r"ECHO\s*\((\d{1,2}/\d{1,2}/\d{2,4})\)\s*:(.*?)(?:\n<a id=|COURSE IN THE HOSPITAL)", md, flags=re.IGNORECASE | re.DOTALL)
    if echo_match:
        echo_text = _clean_text(echo_match.group(2))
        heuristics["clinical_episode"]["echo_date"] = echo_match.group(1)
        heuristics["clinical_episode"]["echo_summary"] = echo_text
        lvef_echo_match = re.search(r"EF\s*[-:=]?\s*(\d{1,2}(?:\.\d+)?)\s*%", echo_text, flags=re.IGNORECASE)
        if lvef_echo_match:
            heuristics["clinical_episode"]["lvef_percent"] = float(lvef_echo_match.group(1))

    followup_cell_match = re.search(r"FOLLOW\s*UP</td><td[^>]*>(.*?)</td>", md, flags=re.IGNORECASE | re.DOTALL)
    followup_text = _clean_text(followup_cell_match.group(1)) if followup_cell_match else ""
    if not followup_text:
        followup_line_match = re.search(r"(REVIEW\s+WITH\s+DR\.?[^<\n]{10,300})", md, flags=re.IGNORECASE)
        if followup_line_match:
            followup_text = _clean_text(followup_line_match.group(1))

    if followup_text:
        heuristics["follow_up"]["text"] = followup_text
        followup_date = _extract_first_date(followup_text)
        if followup_date:
            heuristics["follow_up"]["date"] = followup_date

        doctor_match = re.search(
            r"REVIEW\s+WITH\s+DR\.?\s*([A-Z][A-Z\.\s]+?)(?:\s+ON\b|\s*,|\s+IN\b)",
            followup_text,
            flags=re.IGNORECASE,
        )
        if doctor_match:
            heuristics["follow_up"]["doctor"] = _clean_text(doctor_match.group(1))

        upper_follow = followup_text.upper()
        if "REPORTS" in upper_follow and "WITH" in upper_follow:
            pre_reports = followup_text[: upper_follow.rfind("REPORTS")]
            test_chunk = pre_reports.rsplit("WITH", 1)[-1]
            tests = [t.strip(" .") for t in re.split(r",|/|\\bAND\\b", test_chunk, flags=re.IGNORECASE) if t.strip(" .")]
            if tests:
                heuristics["follow_up"]["required_tests"] = tests

    if "doctor" not in heuristics["follow_up"]:
        signature_doc_match = re.search(r"DR\.\s*([A-Z\.\s]+),", md, flags=re.IGNORECASE)
        if signature_doc_match:
            heuristics["follow_up"]["doctor"] = _clean_text(signature_doc_match.group(1))

    diet_match = re.search(r"DIET</td><td[^>]*>(.*?)</td>", md, flags=re.IGNORECASE | re.DOTALL)
    if diet_match:
        heuristics["advice"]["diet"] = _clean_text(diet_match.group(1))

    fluid_match = re.search(
        r"RESTRICTED FLUID.*?</td><td[^>]*>(.*?)</td>",
        md,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fluid_match:
        heuristics["advice"]["fluid"] = _clean_text(fluid_match.group(1))

    activity_match = re.search(r"PHYSICAL ACTIVITY</td><td[^>]*>(.*?)</td>", md, flags=re.IGNORECASE | re.DOTALL)
    if activity_match:
        heuristics["advice"]["activity"] = _clean_text(activity_match.group(1))

    emergency_table_match = re.search(r"<table id=\"7-1\">(.*?)</table>", md, flags=re.IGNORECASE | re.DOTALL)
    if emergency_table_match:
        first_col_items = re.findall(r"<tr><td[^>]*>(.*?)</td><td[^>]*>", emergency_table_match.group(1), flags=re.IGNORECASE | re.DOTALL)
        cleaned = [_clean_text(x) for x in first_col_items]
        heuristics["emergency_signs"] = [x for x in cleaned if x and "Please call in case" not in x and "appointment" not in x.lower()]

    heuristics["medications"] = _extract_medications_from_markdown(md)
    return heuristics


def _detect_chf_classification(diagnosis_texts: list[str]) -> str:
    joined = " ".join(diagnosis_texts).lower()
    if "hfr" in joined:
        return "HFrEF"
    if "hfmr" in joined:
        return "HFmrEF"
    if "hfp" in joined:
        return "HFpEF"
    if "heart failure" in joined or "lv systolic dysfunction" in joined:
        return "HFrEF"
    return "unknown"


def _normalize_medication_rows(raw_rows: list[Any], heuristic_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    def normalized_med_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())

    def merge_indications(
        base_rows: list[dict[str, str]],
        source_rows: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        source_map: dict[str, str] = {}
        for row in source_rows:
            name_key = normalized_med_name(str(row.get("medication_name", "")))
            indication = str(row.get("indication", "")).strip()
            if name_key and not _is_unknown(indication):
                source_map[name_key] = indication

        merged: list[dict[str, str]] = []
        for row in base_rows:
            out = dict(row)
            name_key = normalized_med_name(str(out.get("medication_name", "")))
            if name_key and _is_unknown(out.get("indication")) and name_key in source_map:
                out["indication"] = source_map[name_key]
            merged.append(out)
        return merged

    def med_item(m: Any) -> dict[str, str]:
        if not isinstance(m, dict):
            return {
                "medication_name": "unknown",
                "dose": "unknown",
                "route": "unknown",
                "frequency": "unknown",
                "indication": "unknown",
            }
        return {
            "medication_name": str(_pick(m, "medication_name", "name", default="unknown")),
            "dose": str(_pick(m, "dose", "dosage", default="unknown")),
            "route": str(_pick(m, "route", default="unknown")),
            "frequency": str(_pick(m, "frequency", default="unknown")),
            "indication": str(_pick(m, "indication", default="unknown")),
        }

    normalized_raw = [med_item(x) for x in raw_rows] if raw_rows else []
    if not normalized_raw and heuristic_rows:
        return heuristic_rows

    unknown_rows = 0
    for med in normalized_raw:
        if any(_is_unknown(med.get(k)) for k in ("medication_name", "dose", "route", "frequency")):
            unknown_rows += 1

    if heuristic_rows and (unknown_rows > 0 or len(heuristic_rows) > len(normalized_raw)):
        return merge_indications(heuristic_rows, normalized_raw)

    if normalized_raw:
        return merge_indications(normalized_raw, heuristic_rows)

    return [{
        "medication_name": "unknown",
        "dose": "unknown",
        "route": "unknown",
        "frequency": "unknown",
        "indication": "unknown",
    }]


def _normalize_to_schema(raw: dict[str, Any], ocr: dict[str, Any], pdf_name: str, heuristics: dict[str, Any]) -> dict[str, Any]:
    patient = raw.get("patient", {}) if isinstance(raw.get("patient"), dict) else {}
    clinical = raw.get("clinical_episode", {}) if isinstance(raw.get("clinical_episode"), dict) else {}
    if not clinical and isinstance(raw.get("clinical_summary"), dict):
        clinical = raw["clinical_summary"]

    follow = raw.get("follow_up", {}) if isinstance(raw.get("follow_up"), dict) else {}
    poc = raw.get("plan_of_care", {}) if isinstance(raw.get("plan_of_care"), dict) else {}
    discharge_info = raw.get("discharge_information", {}) if isinstance(raw.get("discharge_information"), dict) else {}

    heuristic_patient = heuristics.get("patient", {}) if isinstance(heuristics.get("patient"), dict) else {}
    heuristic_encounter = heuristics.get("encounter", {}) if isinstance(heuristics.get("encounter"), dict) else {}
    heuristic_clinical = heuristics.get("clinical_episode", {}) if isinstance(heuristics.get("clinical_episode"), dict) else {}
    heuristic_followup = heuristics.get("follow_up", {}) if isinstance(heuristics.get("follow_up"), dict) else {}
    heuristic_advice = heuristics.get("advice", {}) if isinstance(heuristics.get("advice"), dict) else {}
    emergency_signs = heuristics.get("emergency_signs", []) if isinstance(heuristics.get("emergency_signs"), list) else []

    meds_container = raw.get("medications")
    raw_meds: list[Any] = []
    if isinstance(meds_container, dict):
        raw_meds = meds_container.get("discharge_medications", []) or []
    elif isinstance(meds_container, list):
        raw_meds = meds_container

    meds = _normalize_medication_rows(raw_meds, heuristics.get("medications", []))

    diag_primary = _pick(clinical, "primary_diagnosis", "discharge_diagnosis", default=None)
    diag_list: list[str] = []
    if isinstance(diag_primary, list):
        diag_list = [str(x) for x in diag_primary if x]
    elif not _is_unknown(diag_primary):
        diag_list = [str(diag_primary)]

    secondary = _pick(clinical, "secondary_diagnoses", default=[])
    if isinstance(secondary, list):
        diag_list.extend(str(x) for x in secondary if x)

    heuristic_diag_list = [str(x) for x in heuristic_clinical.get("diagnoses", []) if x] if heuristic_clinical.get("diagnoses") else []
    if not diag_list or all(_is_unknown(x) for x in diag_list):
        diag_list = heuristic_diag_list
    else:
        seen = {x.strip().lower() for x in diag_list if isinstance(x, str)}
        for item in heuristic_diag_list:
            key = item.strip().lower()
            if key and key not in seen:
                diag_list.append(item)
                seen.add(key)

    diag_list = [x for x in diag_list if not _is_unknown(x)]
    primary_diag = diag_list[0] if diag_list else "unknown"

    raw_admit = _pick(raw.get("encounter", {}), "admission_datetime", default=None)
    raw_discharge = _pick(raw.get("encounter", {}), "discharge_datetime", default=None)
    admit_dt = _parse_datetime(raw_admit)
    discharge_dt = _parse_datetime(raw_discharge)

    if not admit_dt:
        admit_dt = _parse_datetime(_pick(discharge_info, "date_of_admission", default=None))
    if not discharge_dt:
        discharge_dt = _parse_datetime(_pick(discharge_info, "date_of_discharge", default=None))
    if not admit_dt:
        admit_dt = _parse_datetime(_pick(heuristic_encounter, "admission_date", default=None))
    if not discharge_dt:
        discharge_dt = _parse_datetime(_pick(heuristic_encounter, "discharge_date", default=None))

    if not discharge_dt:
        discharge_dt = datetime.now().replace(microsecond=0).isoformat()
    if not admit_dt:
        admit_dt = (datetime.fromisoformat(discharge_dt) - timedelta(days=3)).replace(microsecond=0).isoformat()

    reason_for_hospitalization = _first_known(
        _pick(clinical, "reason_for_hospitalization", "presenting_complaints", default=None),
        heuristic_clinical.get("reason_for_hospitalization"),
        default="unknown",
    )
    hospital_course_summary = _first_known(
        _pick(clinical, "hospital_course_summary", "hospital_course", default=None),
        heuristic_clinical.get("hospital_course_summary"),
        default="unknown",
    )

    appointments = follow.get("appointments", []) if isinstance(follow, dict) else []
    if not appointments:
        appt_text = _first_known(_pick(poc, "follow_up", default=None), heuristic_followup.get("text"), default="")
        appt_date = _parse_datetime(_extract_first_date(appt_text)) if isinstance(appt_text, str) else None
        appointments = [
            {
                "appointment_type": "cardiology",
                "status": "scheduled" if appt_date else "pending",
                "scheduled_datetime": appt_date,
                "provider_name": heuristic_followup.get("doctor"),
            }
        ]

    normalized_appointments = []
    for a in appointments:
        if not isinstance(a, dict):
            continue
        dt = _parse_datetime(a.get("scheduled_datetime")) if a.get("scheduled_datetime") else None
        if not dt and heuristic_followup.get("date"):
            dt = _parse_datetime(str(heuristic_followup.get("date")))

        status = a.get("status") if a.get("status") in {"scheduled", "requested", "pending", "completed", "cancelled", "unknown"} else "unknown"
        if dt and status in {"pending", "unknown", "requested"}:
            status = "scheduled"

        normalized_appointments.append(
            {
                "appointment_type": str(a.get("appointment_type") or "cardiology"),
                "status": status,
                "scheduled_datetime": dt,
                "provider_name": _first_known(a.get("provider_name"), heuristic_followup.get("doctor"), default=None),
            }
        )

    if not normalized_appointments:
        normalized_appointments = [
            {
                "appointment_type": "cardiology",
                "status": "scheduled" if heuristic_followup.get("date") else "pending",
                "scheduled_datetime": _parse_datetime(str(heuristic_followup.get("date"))) if heuristic_followup.get("date") else None,
                "provider_name": heuristic_followup.get("doctor"),
            }
        ]

    patient_name = _first_known(_pick(patient, "full_name", "name", default=None), heuristic_patient.get("full_name"), default="UNKNOWN")
    sex_raw = _first_known(_pick(patient, "sex_at_birth", "gender", default=None), heuristic_patient.get("sex_at_birth"), default="unknown")
    sex_norm = str(sex_raw).strip().lower()
    if sex_norm in {"m", "male"}:
        sex_norm = "male"
    elif sex_norm in {"f", "female"}:
        sex_norm = "female"
    else:
        sex_norm = "unknown"

    dob_iso = _parse_date(patient.get("dob"))

    care_start = discharge_dt[:10]
    care_end = (datetime.fromisoformat(discharge_dt) + timedelta(days=90)).date().isoformat()

    missing_hard: list[str] = []
    if _is_unknown(patient_name):
        missing_hard.append("patient.full_name")
    if not dob_iso:
        missing_hard.append("patient.dob")

    soft_missing: list[str] = []
    if _is_unknown(reason_for_hospitalization):
        soft_missing.append("clinical_episode.reason_for_hospitalization")
    if _is_unknown(primary_diag):
        soft_missing.append("clinical_episode.primary_diagnosis")
    if not normalized_appointments[0].get("scheduled_datetime"):
        soft_missing.append("follow_up.appointments[0].scheduled_datetime")
    if _is_unknown(normalized_appointments[0].get("provider_name")):
        soft_missing.append("follow_up.appointments[0].provider_name")

    chf_class = _detect_chf_classification(diag_list)
    diagnosis_confirmed = any("heart failure" in d.lower() for d in diag_list)
    followup_datetime = next((a.get("scheduled_datetime") for a in normalized_appointments if a.get("scheduled_datetime")), None)
    lvef_percent = heuristic_clinical.get("lvef_percent")
    if not isinstance(lvef_percent, (int, float)):
        lvef_percent = None

    if lvef_percent is not None and 0 <= float(lvef_percent) <= 100:
        lvef_percent = float(lvef_percent)
    else:
        lvef_percent = None

    output = {
        "schema_version": "1.0.0",
        "source_document": {
            "file_name": pdf_name,
            "page_count": int(_pick(raw.get("source_document", {}), "page_count", default=_pick(ocr, "page_count", default=1))),
            "ocr_quality_score": float(_pick(raw.get("source_document", {}), "ocr_quality_score", default=_pick(ocr, "ocr_quality_score", default=0.5))),
            "illegible_sections": list(_pick(raw.get("source_document", {}), "illegible_sections", default=_pick(ocr, "illegible_sections", default=[]))),
        },
        "patient": {
            "full_name": str(patient_name),
            "dob": dob_iso or "1900-01-01",
            "sex_at_birth": sex_norm,
            "mrn": _first_known(_pick(patient, "mrn", default=None), heuristic_patient.get("mrn"), default=None),
        },
        "encounter": {
            "facility_name": str(
                _first_known(_pick(raw.get("encounter", {}), "facility_name", default=None), heuristic_encounter.get("facility_name"), default="unknown facility")
            ),
            "admission_datetime": admit_dt,
            "discharge_datetime": discharge_dt,
            "disposition": str(_pick(raw.get("encounter", {}), "disposition", default="home")),
        },
        "clinical_episode": {
            "reason_for_hospitalization": str(reason_for_hospitalization),
            "primary_diagnosis": str(primary_diag),
            "secondary_diagnoses": diag_list[1:] if len(diag_list) > 1 else [],
            "hospital_course_summary": str(hospital_course_summary),
            "discharge_condition": str(_pick(clinical, "discharge_condition", default="improving" if "discharged" in str(hospital_course_summary).lower() else "unknown")),
        },
        "medications": {
            "discharge_medications": meds,
            "allergies": list(_pick(raw.get("medications", {}), "allergies", default=[])) if isinstance(raw.get("medications"), dict) else [],
        },
        "follow_up": {
            "appointments": normalized_appointments,
            "care_coordinator": {
                "name": "To be assigned",
                "role": "nurse care coordinator",
                "phone": None,
            },
        },
        "care_plan_90d": {
            "start_date": care_start,
            "end_date": care_end,
            "phase_0_7": [
                "Daily symptom check and weight logging",
                "Medication reconciliation and adherence call",
                "Schedule/confirm cardiology follow-up within 7 days",
                "Follow discharge advice for diet/fluid/activity restrictions",
            ],
            "phase_8_30": [
                "Continue daily weights and BP",
                "Titrate CHF medications per clinician plan",
                "Weekly nurse check-in",
            ],
            "phase_31_90": [
                "Self-management reinforcement",
                "Monitor decompensation red flags",
                "Chronic disease optimization follow-up",
            ],
            "trigger_action_rules": [
                "Weight gain > 1 kg in 24h or > 2 kg in 7d triggers nurse call",
                "Severe dyspnea/chest pain/syncope triggers ED/911 escalation",
            ],
        },
        "clinical_modules": {
            "chf": {
                "enabled": True,
                "diagnosis_confirmed": diagnosis_confirmed,
                "hf_phenotype": {
                    "classification": chf_class,
                    "latest_lvef_percent": lvef_percent,
                },
                "congestion_status": {
                    "euvolemic_at_discharge": False,
                    "discharge_weight_kg": None,
                    "target_dry_weight_kg": None,
                },
                "gdmt": {
                    "medication_classes": [
                        {"class": "arni_or_acei_or_arb", "status": "unknown", "drug_name": None},
                        {"class": "beta_blocker_hf_evidence", "status": "unknown", "drug_name": None},
                        {"class": "mra", "status": "unknown", "drug_name": None},
                        {"class": "sglt2i", "status": "unknown", "drug_name": None},
                        {"class": "loop_diuretic", "status": "unknown", "drug_name": None},
                    ]
                },
                "monitoring": {
                    "daily_weight_required": True,
                    "bp_required": True,
                    "heart_rate_required": True,
                    "symptom_check_required": True,
                    "lab_frequency": "within 1 week then per clinician",
                },
                "follow_up": {
                    "hf_followup_scheduled": any(a.get("status") == "scheduled" for a in normalized_appointments),
                    "followup_within_7_days_target": True,
                    "followup_datetime": followup_datetime,
                },
                "red_flags": {
                    "yellow_zone": emergency_signs[:4]
                    if emergency_signs
                    else [
                        "increasing leg swelling",
                        "weight gain over threshold",
                        "worsening exertional breathlessness",
                    ],
                    "red_zone": emergency_signs[4:8]
                    if len(emergency_signs) >= 6
                    else [
                        "resting breathlessness",
                        "chest pain",
                        "syncope",
                    ],
                    "weight_gain_trigger_24h_kg": 1.0,
                    "weight_gain_trigger_7d_kg": 2.0,
                },
                "validation": {
                    "hard_stop_complete": False,
                    "hard_stop_missing_fields": [
                        "clinical_modules.chf.congestion_status.discharge_weight_kg",
                    ]
                    + (["clinical_modules.chf.hf_phenotype.latest_lvef_percent"] if lvef_percent is None else [])
                    + (["clinical_modules.chf.follow_up.followup_datetime"] if not followup_datetime else []),
                },
            }
        },
        "extracted_details": {
            "patient": heuristic_patient,
            "encounter": heuristic_encounter,
            "clinical_episode": heuristic_clinical,
            "follow_up": heuristic_followup,
            "discharge_advice": heuristic_advice,
            "emergency_signs": emergency_signs,
            "medication_rows_from_markdown": heuristics.get("medications", []),
        },
        "validation": {
            "hard_stop_complete": len(missing_hard) == 0,
            "hard_stop_missing_fields": missing_hard,
            "soft_stop_missing_fields": list(dict.fromkeys(
                soft_missing
                + [
                    "follow_up.care_coordinator.phone",
                ]
                + (["clinical_modules.chf.hf_phenotype.latest_lvef_percent"] if lvef_percent is None else [])
            )),
            "ready_for_patient_app": len(missing_hard) == 0 and not _is_unknown(primary_diag) and followup_datetime is not None,
        },
    }

    return output


def _enrich_medication_indications_with_gemini(
    extracted: dict[str, Any],
    gemini_client: GeminiClient,
    json_model: str,
) -> dict[str, Any]:
    meds_container = extracted.get("medications", {})
    if not isinstance(meds_container, dict):
        return extracted

    meds = meds_container.get("discharge_medications", [])
    if not isinstance(meds, list) or not meds:
        return extracted

    targets: list[dict[str, Any]] = []
    for idx, med in enumerate(meds):
        if not isinstance(med, dict):
            continue
        if _is_unknown(med.get("medication_name")):
            continue
        if _is_unknown(med.get("indication")):
            targets.append(
                {
                    "row_index": idx,
                    "medication_name": med.get("medication_name"),
                    "dose": med.get("dose"),
                    "route": med.get("route"),
                    "frequency": med.get("frequency"),
                }
            )

    if not targets:
        return extracted

    clinical = extracted.get("clinical_episode", {}) if isinstance(extracted.get("clinical_episode"), dict) else {}
    context = {
        "primary_diagnosis": clinical.get("primary_diagnosis"),
        "secondary_diagnoses": clinical.get("secondary_diagnoses", []),
        "reason_for_hospitalization": clinical.get("reason_for_hospitalization"),
        "medications_needing_purpose": targets,
    }

    prompt = f"""
Infer the most likely purpose for each medication row based on diagnosis/context and common usage.
Return strict JSON with this shape:
{{
  "items": [
    {{
      "row_index": 0,
      "indication": "short purpose"
    }}
  ]
}}
Use each row_index from input exactly once.
Context:
{json.dumps(context, ensure_ascii=True)}
""".strip()

    try:
        inferred = gemini_client.generate_json(
            model=json_model,
            system_prompt=MEDICATION_INDICATION_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.0,
        )
    except GeminiError:
        return extracted

    items = inferred.get("items", [])
    if not isinstance(items, list):
        return extracted

    for item in items:
        if not isinstance(item, dict):
            continue
        row_index = item.get("row_index")
        indication = str(item.get("indication", "")).strip()
        if not isinstance(row_index, int):
            continue
        if row_index < 0 or row_index >= len(meds):
            continue
        if _is_unknown(indication):
            continue
        if isinstance(meds[row_index], dict):
            meds[row_index]["indication"] = indication

    return extracted


def run_extraction(
    pdf_path: str,
    model_ocr: str | None = None,
    model_json: str | None = None,
    api_key: str | None = None,
    landing_api_key: str | None = None,
) -> dict[str, Any]:
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    gemini_client = GeminiClient(api_key=api_key)
    landing_client = LandingClient(api_key=landing_api_key)
    raw_pdf = pdf_file.read_bytes()

    parse_model = model_ocr or settings.landing_parse_model or None
    json_model = model_json or settings.gemini_json_model

    try:
        parsed = landing_client.parse_document(raw_pdf, filename=pdf_file.name, model=parse_model)
    except LandingError as exc:
        raise GeminiError(str(exc)) from exc

    ocr_result = _landing_to_ocr_payload(parsed)
    parsed_markdown = str(parsed.get("markdown", "") or "")
    markdown_for_prompt = parsed_markdown[:120000]
    heuristics = _extract_markdown_heuristics(parsed_markdown)

    extraction_user_prompt = f"""
Use the parsed discharge summary markdown below to produce structured JSON for discharge + CHF care planning.
Medication extraction quality is critical.
- If medication rows are present in markdown, do not use "unknown" for medication_name/dose/frequency/route.
- Parse follow-up physician and follow-up date if present.

High-confidence heuristic hints (use unless contradicted by source):
{json.dumps(heuristics, ensure_ascii=True)}

Input Parsed Markdown:
{markdown_for_prompt}

Try to include keys aligned with this structure:
- patient
- encounter
- clinical_episode or clinical_summary
- medications
- follow_up or plan_of_care
- source_document
- validation
- clinical_modules
""".strip()

    extracted_raw: dict[str, Any]
    llm_failed = False
    try:
        extracted_raw = gemini_client.generate_json(
            model=json_model,
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_prompt=extraction_user_prompt,
            temperature=0.1,
        )
    except GeminiError:
        # Keep pipeline resilient: deterministic parser still builds a high-quality baseline.
        extracted_raw = {}
        llm_failed = True

    extracted = _normalize_to_schema(extracted_raw, ocr_result, pdf_file.name, heuristics)
    extracted = _enrich_medication_indications_with_gemini(extracted, gemini_client, json_model)
    if llm_failed:
        extracted["validation"]["soft_stop_missing_fields"].append("llm.extraction_fallback_used")

    schema = _load_schema()
    try:
        validate(instance=extracted, schema=schema)
    except ValidationError as exc:
        raise GeminiError(f"Extracted JSON failed schema validation: {exc.message}") from exc

    return extracted
