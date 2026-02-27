from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.gemini_client import GeminiError
from app.config import settings
from app.pipeline import run_extraction
from app.storage import get_extraction, init_db, list_extractions, save_extraction
from app.summary import build_simplified_summary

logger = logging.getLogger(__name__)

app = FastAPI(title="Sidiya — Discharge Care Platform", version="0.2.0")

# CORS for PWA
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import hashlib as _hashlib

_static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# Cache-bust token: hash of css+js content at startup
_bust = _hashlib.md5(
    b"".join(p.read_bytes() for p in sorted(_static_dir.glob("*")) if p.is_file()),
    usedforsecurity=False,
).hexdigest()[:8]
templates.env.globals["v"] = _bust


@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=60"
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.on_event("startup")
def startup_event() -> None:
    init_db()


# ═══════════════════════════════════════════════════════════════════════════
# Existing routes (extraction dashboard)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/care-plan", response_class=HTMLResponse)
def care_plan_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("care_plan.html", {"request": request})


@app.get("/calendar-view", response_class=HTMLResponse)
def calendar_view_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("calendar_view.html", {"request": request})


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("history.html", {"request": request})


@app.get("/summary/{extraction_id}", response_class=HTMLResponse)
def summary_page(request: Request, extraction_id: int) -> HTMLResponse:
    record = get_extraction(extraction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return templates.TemplateResponse(
        "summary.html",
        {
            "request": request,
            "record": record,
        },
    )


@app.get("/careplan")
def care_plan_alias() -> RedirectResponse:
    return RedirectResponse(url="/care-plan", status_code=307)


# ═══════════════════════════════════════════════════════════════════════════
# Patient PWA routes
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/patient", response_class=HTMLResponse)
def patient_app(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("patient_app.html", {"request": request})


@app.get("/provider", response_class=HTMLResponse)
def provider_dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("provider_dashboard.html", {"request": request})


# ═══════════════════════════════════════════════════════════════════════════
# API: Health & Extractions (existing)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "config": {
            "gemini_api_key_configured": bool(settings.gemini_api_key),
            "landing_api_key_configured": bool(settings.landing_api_key),
            "firebase_configured": bool(settings.firebase_credentials_path),
            "twilio_configured": bool(settings.twilio_account_sid),
        },
    }


@app.get("/api/extractions")
def extractions_api(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"items": list_extractions(limit=limit)}


@app.get("/api/extractions/{extraction_id}")
def extraction_by_id_api(extraction_id: int) -> dict:
    record = get_extraction(extraction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return record


@app.post("/extract")
async def extract(pdf: UploadFile = File(...)) -> dict:
    if pdf.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Upload a PDF file.")

    suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        raw = await pdf.read()
        tmp.write(raw)
        tmp.flush()

        try:
            output = run_extraction(tmp.name)
        except (GeminiError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}") from exc

    summary_text = build_simplified_summary(output)
    extraction_id = save_extraction(output, summary_text)
    output["extraction_id"] = extraction_id
    output["simplified_summary"] = summary_text
    return output


# ═══════════════════════════════════════════════════════════════════════════
# API: Patient Registration
# ═══════════════════════════════════════════════════════════════════════════

class PatientRegisterRequest(BaseModel):
    extraction_id: int
    phone: str
    caregiver_phone: str | None = None
    nurse_phone: str | None = None


@app.post("/api/patients/register")
def register_patient(req: PatientRegisterRequest) -> dict:
    """Register a patient from an extraction and generate reminder rules."""
    from app import firestore_client as fdb
    from app.reminder_engine import generate_reminder_rules

    # Get the extraction
    record = get_extraction(req.extraction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction not found")

    extraction = record["extraction_json"]

    # Check if patient already registered for this extraction
    existing = fdb.get_patient_by_extraction(req.extraction_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Patient already registered (ID: {existing['id']})")

    # Create patient in Firestore
    patient_data = {
        "extraction_id": req.extraction_id,
        "full_name": extraction.get("patient", {}).get("full_name", "Unknown"),
        "dob": extraction.get("patient", {}).get("dob"),
        "sex": extraction.get("patient", {}).get("sex_at_birth"),
        "mrn": extraction.get("patient", {}).get("mrn"),
        "phone": req.phone,
        "caregiver_phone": req.caregiver_phone,
        "nurse_phone": req.nurse_phone,
        "primary_diagnosis": extraction.get("clinical_episode", {}).get("primary_diagnosis"),
        "care_plan_start_date": extraction.get("care_plan_90d", {}).get("start_date"),
        "care_plan_end_date": extraction.get("care_plan_90d", {}).get("end_date"),
    }
    patient_id = fdb.create_patient(patient_data)

    # Generate reminder rules from extraction
    rule_counts = generate_reminder_rules(patient_id, extraction)

    return {
        "patient_id": patient_id,
        "full_name": patient_data["full_name"],
        "phone": req.phone,
        "reminder_rules_created": rule_counts,
    }


# ═══════════════════════════════════════════════════════════════════════════
# API: Patient App Endpoints
# ═══════════════════════════════════════════════════════════════════════════

def _get_patient_or_404(patient_id: str) -> dict[str, Any]:
    from app import firestore_client as fdb
    patient = fdb.get_patient(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@app.get("/api/patient/{patient_id}/today")
def patient_today(patient_id: str) -> dict:
    """Get patient's today view: pending tasks, vitals, medications."""
    from app import firestore_client as fdb
    from app.reminder_engine import _parse_frequency_to_times

    patient = _get_patient_or_404(patient_id)

    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_iso = now.strftime("%Y-%m-%d")

    # Compute care plan day
    care_plan_day = 0
    phase = "0-7"
    care_start = patient.get("care_plan_start_date")
    if care_start:
        try:
            start = datetime.fromisoformat(care_start).date()
            current = now.date() if hasattr(now, "date") else datetime.fromisoformat(today_iso).date()
            care_plan_day = (current - start).days
            if care_plan_day <= 7:
                phase = "0-7"
            elif care_plan_day <= 30:
                phase = "8-30"
            else:
                phase = "31-90"
        except (ValueError, TypeError):
            pass

    # Get today's vitals
    weight_logs = fdb.get_vitals_for_date(patient_id, today_iso, "weight")
    bp_logs = fdb.get_vitals_for_date(patient_id, today_iso, "bp")
    symptom_logs = fdb.get_vitals_for_date(patient_id, today_iso, "symptom_check")

    # Get today's medication status
    med_logs = fdb.get_medication_logs_for_date(patient_id, today_iso)
    logged_meds = {(m.get("medication_name"), m.get("scheduled_time")): m.get("status") for m in med_logs}

    # Build medication schedule from reminder rules
    rules = fdb.get_reminder_rules(patient_id)
    medications = []
    for rule in rules:
        if rule.get("type") != "medication":
            continue
        payload = rule.get("payload", {})
        for t in rule.get("schedule", {}).get("times", []):
            key = (payload.get("medication_name"), t)
            status = logged_meds.get(key, "pending")
            medications.append({
                "medication_name": payload.get("medication_name"),
                "dose": payload.get("dose"),
                "indication": payload.get("indication"),
                "scheduled_time": t,
                "status": status,
            })
    medications.sort(key=lambda m: m["scheduled_time"])

    # Get upcoming appointment
    next_appointment = None
    for rule in rules:
        if rule.get("type") == "appointment":
            appt_dt = rule.get("payload", {}).get("appointment_datetime")
            if appt_dt:
                next_appointment = {
                    "datetime": appt_dt,
                    "provider": rule.get("payload", {}).get("provider"),
                }
                break

    return {
        "patient_id": patient_id,
        "full_name": patient.get("full_name"),
        "care_plan_day": care_plan_day,
        "phase": phase,
        "date": today_iso,
        "vitals": {
            "weight_logged": len(weight_logs) > 0,
            "weight_value": weight_logs[-1].get("value") if weight_logs else None,
            "bp_logged": len(bp_logs) > 0,
            "bp_value": bp_logs[-1].get("value") if bp_logs else None,
            "symptom_check_done": len(symptom_logs) > 0,
        },
        "medications": medications,
        "next_appointment": next_appointment,
        "thresholds": patient.get("thresholds", {}),
    }


class VitalLogRequest(BaseModel):
    type: str  # "weight" | "bp" | "heart_rate" | "spo2" | "symptom_check"
    value: Any  # number, {systolic, diastolic}, or {symptoms: [...]}


@app.post("/api/patient/{patient_id}/vitals")
def log_patient_vital(patient_id: str, req: VitalLogRequest) -> dict:
    """Log a vital sign and run threshold checks."""
    from app import firestore_client as fdb
    from app.escalation import check_weight_thresholds, check_symptom_red_flags, resolve_escalations_for_action

    _get_patient_or_404(patient_id)

    log_id = fdb.log_vital(patient_id, req.type, req.value)

    result: dict[str, Any] = {"log_id": log_id, "type": req.type}

    # Auto-resolve missed-action escalations
    resolved = resolve_escalations_for_action(patient_id, req.type)
    if resolved:
        result["escalations_resolved"] = resolved

    # Run threshold checks
    if req.type == "weight" and isinstance(req.value, (int, float)):
        esc = check_weight_thresholds(patient_id, float(req.value))
        if esc:
            result["alert"] = esc

    if req.type == "symptom_check" and isinstance(req.value, dict):
        symptoms = req.value.get("symptoms", [])
        if symptoms:
            esc = check_symptom_red_flags(patient_id, symptoms)
            if esc:
                result["alert"] = esc

    return result


class MedicationAckRequest(BaseModel):
    medication_name: str
    scheduled_time: str
    status: str = "taken"  # "taken" | "skipped"
    skip_reason: str | None = None


@app.post("/api/patient/{patient_id}/medications/ack")
def acknowledge_medication(patient_id: str, req: MedicationAckRequest) -> dict:
    """Acknowledge a medication dose as taken or skipped."""
    from app import firestore_client as fdb
    from app.escalation import resolve_escalations_for_action

    _get_patient_or_404(patient_id)

    log_id = fdb.log_medication(
        patient_id, req.medication_name, req.scheduled_time,
        req.status, req.skip_reason,
    )

    result: dict[str, Any] = {"log_id": log_id, "status": req.status}

    if req.status == "taken":
        resolved = resolve_escalations_for_action(
            patient_id, "medication",
            extra={"medication_name": req.medication_name},
        )
        if resolved:
            result["escalations_resolved"] = resolved

    return result


@app.get("/api/patient/{patient_id}/vitals/history")
def patient_vital_history(patient_id: str, vital_type: str = "weight", days: int = 7) -> dict:
    """Get vital history for trend charts."""
    from app import firestore_client as fdb
    _get_patient_or_404(patient_id)
    logs = fdb.get_vitals_range(patient_id, vital_type, days=min(days, 90))
    return {"type": vital_type, "days": days, "logs": logs}


@app.get("/api/patient/{patient_id}/medications")
def patient_medications(patient_id: str) -> dict:
    """Get today's medication schedule."""
    from app import firestore_client as fdb

    _get_patient_or_404(patient_id)

    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_iso = now.strftime("%Y-%m-%d")

    rules = fdb.get_reminder_rules(patient_id)
    med_logs = fdb.get_medication_logs_for_date(patient_id, today_iso)
    logged = {(m.get("medication_name"), m.get("scheduled_time")): m.get("status") for m in med_logs}

    medications = []
    for rule in rules:
        if rule.get("type") != "medication":
            continue
        payload = rule.get("payload", {})
        for t in rule.get("schedule", {}).get("times", []):
            key = (payload.get("medication_name"), t)
            medications.append({
                "medication_name": payload.get("medication_name"),
                "dose": payload.get("dose"),
                "route": payload.get("route"),
                "frequency": payload.get("frequency"),
                "indication": payload.get("indication"),
                "scheduled_time": t,
                "status": logged.get(key, "pending"),
            })
    medications.sort(key=lambda m: m["scheduled_time"])
    return {"date": today_iso, "medications": medications}


@app.get("/api/patient/{patient_id}/care-plan")
def patient_care_plan(patient_id: str) -> dict:
    """Get the patient's care plan with current phase highlighted."""
    from app import firestore_client as fdb

    patient = _get_patient_or_404(patient_id)
    extraction_id = patient.get("extraction_id")
    if not extraction_id:
        raise HTTPException(status_code=404, detail="No extraction linked to patient")

    record = get_extraction(extraction_id)
    if not record:
        raise HTTPException(status_code=404, detail="Extraction not found")

    extraction = record["extraction_json"]
    care_plan = extraction.get("care_plan_90d", {})
    red_flags = extraction.get("clinical_modules", {}).get("chf", {}).get("red_flags", {})
    medications = extraction.get("medications", {})

    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    care_plan_day = 0
    care_start = care_plan.get("start_date")
    if care_start:
        try:
            start = datetime.fromisoformat(care_start).date()
            care_plan_day = (now.date() - start).days if hasattr(now, "date") else 0
        except (ValueError, TypeError):
            pass

    return {
        "care_plan_day": care_plan_day,
        "care_plan": care_plan,
        "red_flags": red_flags,
        "medications": medications,
        "discharge_advice": extraction.get("extracted_details", {}).get("discharge_advice", {}),
    }


# ═══════════════════════════════════════════════════════════════════════════
# API: Provider Dashboard Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/provider/patients")
def provider_patients() -> dict:
    """Get all active patients with today's compliance status."""
    from app import firestore_client as fdb

    patients = fdb.list_active_patients()
    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_iso = now.strftime("%Y-%m-%d")

    results = []
    for p in patients:
        pid = p["id"]
        compliance = fdb.get_daily_compliance(pid, today_iso)
        open_escs = fdb.get_open_escalations(pid)

        # Determine status color
        score = (compliance or {}).get("compliance_score", 0)
        alert_count = len(open_escs)
        if alert_count >= 3 or score < 0.3:
            status = "critical"
        elif alert_count >= 1 or score < 0.6:
            status = "at_risk"
        else:
            status = "good"

        # Compute care plan day
        care_plan_day = 0
        care_start = p.get("care_plan_start_date")
        if care_start:
            try:
                start = datetime.fromisoformat(care_start).date()
                care_plan_day = (now.date() - start).days if hasattr(now, "date") else 0
            except (ValueError, TypeError):
                pass

        results.append({
            "patient_id": pid,
            "full_name": p.get("full_name"),
            "primary_diagnosis": p.get("primary_diagnosis"),
            "phone": p.get("phone"),
            "care_plan_day": care_plan_day,
            "today_compliance": compliance,
            "open_alerts": alert_count,
            "status": status,
        })

    # Sort: critical first, then at_risk, then good
    order = {"critical": 0, "at_risk": 1, "good": 2}
    results.sort(key=lambda r: order.get(r["status"], 3))
    return {"patients": results}


@app.get("/api/provider/alerts")
def provider_alerts() -> dict:
    """Get all open escalations across patients."""
    from app import firestore_client as fdb
    escalations = fdb.get_open_escalations()

    # Enrich with patient names
    for esc in escalations:
        patient = fdb.get_patient(esc.get("patient_id", ""))
        esc["patient_name"] = patient.get("full_name", "Unknown") if patient else "Unknown"

    return {"alerts": escalations}


@app.post("/api/provider/alerts/{escalation_id}/ack")
def acknowledge_alert(escalation_id: str) -> dict:
    """Provider acknowledges an escalation."""
    from app import firestore_client as fdb
    fdb.resolve_escalation(escalation_id, "nurse_ack")
    return {"status": "resolved", "escalation_id": escalation_id}


@app.get("/api/provider/patient/{patient_id}/vitals")
def provider_patient_vitals(patient_id: str, days: int = 7) -> dict:
    """Get patient vitals for provider drill-down (weight + BP trends)."""
    from app import firestore_client as fdb
    _get_patient_or_404(patient_id)

    weight_logs = fdb.get_vitals_range(patient_id, "weight", days=min(days, 90))
    bp_logs = fdb.get_vitals_range(patient_id, "bp", days=min(days, 90))
    compliance = fdb.get_compliance_range(patient_id, days=min(days, 90))

    return {
        "patient_id": patient_id,
        "weight": weight_logs,
        "bp": bp_logs,
        "compliance": compliance,
    }


# ═══════════════════════════════════════════════════════════════════════════
# API: Cron Endpoints (Cloud Scheduler)
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/cron/evaluate")
def cron_evaluate_reminders() -> dict:
    """Evaluate all reminder rules and send due notifications.
    Called by Cloud Scheduler every 5 minutes.
    """
    from app.notifications import evaluate_and_send_reminders
    return evaluate_and_send_reminders()


@app.post("/cron/escalation-check")
def cron_escalation_check() -> dict:
    """Check for missed actions and manage escalation levels.
    Called by Cloud Scheduler every 30 minutes.
    """
    from app.escalation import check_missed_actions
    return check_missed_actions()
