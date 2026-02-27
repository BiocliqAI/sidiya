"""Notification delivery for Sidiya Reminder System.

Multi-channel delivery: FCM push → Twilio SMS (with fallback).
WhatsApp support is planned for Phase 2.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app import firestore_client as fdb

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FCM Push Notifications
# ---------------------------------------------------------------------------

def _send_fcm(device_tokens: list[str], title: str, body: str, data: dict | None = None) -> bool:
    """Send push notification via Firebase Cloud Messaging."""
    if not device_tokens:
        return False

    try:
        from firebase_admin import messaging

        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=data or {},
            tokens=device_tokens,
        )
        response = messaging.send_each_for_multicast(message)
        success = response.success_count > 0
        if response.failure_count > 0:
            logger.warning("FCM: %d/%d failures", response.failure_count, len(device_tokens))
        return success
    except Exception:
        logger.exception("FCM send failed")
        return False


# ---------------------------------------------------------------------------
# Twilio SMS
# ---------------------------------------------------------------------------

def _send_sms(phone: str, body: str) -> bool:
    """Send SMS via Twilio."""
    if not all([settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_phone_number]):
        logger.warning("Twilio not configured, skipping SMS to %s", phone)
        return False
    if not phone:
        return False

    try:
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        message = client.messages.create(
            body=body,
            from_=settings.twilio_phone_number,
            to=phone,
        )
        logger.info("SMS sent to %s, SID: %s", phone, message.sid)
        return True
    except Exception:
        logger.exception("SMS send failed to %s", phone)
        return False


# ---------------------------------------------------------------------------
# Multi-channel delivery with fallback
# ---------------------------------------------------------------------------

def send_notification(
    patient: dict[str, Any],
    title: str,
    body: str,
    notification_type: str,
    rule_id: str | None = None,
    data: dict | None = None,
) -> str:
    """Send notification via preferred channel with fallback.

    Returns the channel used: "push", "sms", or "failed".
    Also logs the notification to Firestore.
    """
    patient_id = patient["id"]
    prefs = patient.get("notification_preferences", {})
    channel = "failed"

    # Try push first
    if prefs.get("push") and patient.get("device_tokens"):
        if _send_fcm(patient["device_tokens"], title, body, data):
            channel = "push"

    # Fallback to SMS
    if channel == "failed" and prefs.get("sms") and patient.get("phone"):
        sms_body = f"Sidiya: {body}"
        if _send_sms(patient["phone"], sms_body):
            channel = "sms"

    # Log notification to Firestore
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fdb.log_notification(patient_id, {
        "rule_id": rule_id,
        "type": notification_type,
        "channel": channel,
        "title": title,
        "message_text": body,
        "status": "sent" if channel != "failed" else "failed",
        "date": today,
    })

    if channel == "failed":
        logger.error("All notification channels failed for patient %s", patient_id)
    else:
        logger.info("Notification sent via %s to patient %s: %s", channel, patient_id, title)

    return channel


# ---------------------------------------------------------------------------
# Cron: Evaluate and send due reminders
# ---------------------------------------------------------------------------

def evaluate_and_send_reminders() -> dict[str, int]:
    """Main cron job: evaluate all reminder rules and send due notifications.

    Called by Cloud Scheduler every 5 minutes.
    Returns summary counts.
    """
    now = datetime.now(timezone.utc)
    # Convert to IST for Indian patients (UTC+5:30)
    from datetime import timedelta
    ist_now = now + timedelta(hours=5, minutes=30)
    current_time = ist_now.strftime("%H:%M")
    today_iso = ist_now.strftime("%Y-%m-%d")

    stats = {"evaluated": 0, "sent": 0, "skipped": 0, "failed": 0}

    patients = fdb.list_active_patients()
    for patient in patients:
        patient_id = patient["id"]
        rules = fdb.get_reminder_rules(patient_id)

        for rule in rules:
            stats["evaluated"] += 1
            rule_id = rule.get("id")
            schedule = rule.get("schedule", {})
            times = schedule.get("times", [])
            days = schedule.get("days", "daily")

            # Check if this rule should fire now
            if not _is_rule_due(current_time, today_iso, times, days):
                continue

            # Check if already sent today for this rule
            existing = fdb.get_notifications_for_date(patient_id, today_iso, rule_id)
            if existing:
                stats["skipped"] += 1
                continue

            # Build notification content
            title, body = _build_notification_content(rule)

            # Skip nurse-targeted reminders for patient delivery
            if rule.get("target") == "nurse":
                stats["skipped"] += 1
                continue

            channel = send_notification(
                patient=patient,
                title=title,
                body=body,
                notification_type=rule["type"],
                rule_id=rule_id,
            )
            if channel != "failed":
                stats["sent"] += 1
            else:
                stats["failed"] += 1

    logger.info("Reminder evaluation complete: %s", stats)
    return stats


def _is_rule_due(current_time: str, today_iso: str, times: list[str], days: Any) -> bool:
    """Check if a reminder rule should fire at the current time."""
    # Check time window (within 5 minutes of scheduled time)
    current_h, current_m = map(int, current_time.split(":"))
    current_minutes = current_h * 60 + current_m

    time_match = False
    for t in times:
        try:
            h, m = map(int, t.split(":"))
            scheduled_minutes = h * 60 + m
            if abs(current_minutes - scheduled_minutes) <= 2:  # 2-minute window
                time_match = True
                break
        except (ValueError, TypeError):
            continue

    if not time_match:
        return False

    # Check day
    if days == "daily":
        return True
    if isinstance(days, list):
        return today_iso in days
    if days == "weekly":
        # Fire on the same weekday as the first scheduled day
        return True

    return True


def _build_notification_content(rule: dict[str, Any]) -> tuple[str, str]:
    """Build title and body for a notification based on rule type."""
    rule_type = rule.get("type", "")
    payload = rule.get("payload", {})

    if rule_type == "medication":
        med_name = payload.get("medication_name", "your medication")
        dose = payload.get("dose", "")
        indication = payload.get("indication", "")
        title = "Medication Reminder"
        body = f"Time to take {med_name}"
        if dose and dose != "unknown":
            body += f" ({dose})"
        if indication and indication != "unknown":
            body += f" — {indication}"
        return title, body

    if rule_type == "weight":
        title = "Weight Check"
        body = payload.get("message", "Please log your weight.")
        return title, body

    if rule_type == "bp":
        title = "BP Check"
        body = payload.get("message", "Please log your blood pressure.")
        return title, body

    if rule_type == "symptom_check":
        title = "Evening Check-in"
        body = payload.get("message", "How are you feeling today?")
        return title, body

    if rule_type == "appointment":
        title = "Appointment Reminder"
        body = payload.get("message", "You have an upcoming appointment.")
        return title, body

    if rule_type == "nurse_checkin":
        title = "Nurse Check-in"
        body = payload.get("message", "Nurse check-in scheduled for today.")
        return title, body

    return "Sidiya Reminder", payload.get("message", "You have a pending task.")
