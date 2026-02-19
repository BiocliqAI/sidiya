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


settings = Settings()
