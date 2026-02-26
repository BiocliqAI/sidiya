from __future__ import annotations

from typing import Any

import requests

from app.config import settings


class LandingError(RuntimeError):
    pass


class LandingClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.landing_api_key
        if not self.api_key:
            raise LandingError("LANDINGAI_API_KEY is missing. Set it in environment.")

    def parse_document(self, pdf_bytes: bytes, filename: str, model: str | None = None) -> dict[str, Any]:
        files = {
            "document": (filename, pdf_bytes, "application/pdf"),
        }
        model_name = model or settings.landing_parse_model
        data: dict[str, str] = {}
        if model_name:
            data["model"] = model_name
        headers = {"Authorization": f"Bearer {self.api_key}"}

        response = requests.post(
            f"{settings.landing_api_base}/v1/ade/parse",
            headers=headers,
            data=data,
            files=files,
            timeout=180,
        )
        if response.status_code >= 300:
            raise LandingError(f"Landing Parse error {response.status_code}: {response.text[:500]}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise LandingError("Landing Parse returned non-object JSON payload")
        if "markdown" not in payload:
            raise LandingError("Landing Parse response missing markdown")
        return payload
