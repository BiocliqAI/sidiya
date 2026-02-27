from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_json_model: str = os.getenv("GEMINI_JSON_MODEL", "gemini-2.0-flash")
    gemini_api_base: str = os.getenv(
        "GEMINI_API_BASE",
        "https://generativelanguage.googleapis.com/v1beta/models",
    )
    landing_api_key: str | None = os.getenv("LANDINGAI_API_KEY")
    landing_parse_model: str = os.getenv("LANDINGAI_PARSE_MODEL", "")
    landing_api_base: str = os.getenv("LANDINGAI_API_BASE", "https://api.va.landing.ai")

    # Firebase
    firebase_credentials_path: str = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-admin-key.json")
    firebase_project_id: str = os.getenv("FIREBASE_PROJECT_ID", "sidiya-672f1")

    # Twilio SMS
    twilio_account_sid: str | None = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_phone_number: str | None = os.getenv("TWILIO_PHONE_NUMBER")


settings = Settings()
