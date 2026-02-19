from __future__ import annotations

import base64
import json
import re
from typing import Any

import requests

from app.config import settings


class GeminiError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.gemini_api_key
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY is missing. Set it in environment or .env.")

    def generate_json(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        inline_mime_type: str | None = None,
        inline_bytes: bytes | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": user_prompt}]

        if inline_mime_type and inline_bytes:
            parts.insert(
                0,
                {
                    "inline_data": {
                        "mime_type": inline_mime_type,
                        "data": base64.b64encode(inline_bytes).decode("utf-8"),
                    }
                },
            )

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "response_mime_type": "application/json",
            },
        }

        url = f"{settings.gemini_api_base}/{model}:generateContent?key={self.api_key}"
        response = requests.post(url, json=payload, timeout=120)

        if response.status_code >= 300:
            raise GeminiError(f"Gemini API error {response.status_code}: {response.text[:500]}")

        data = response.json()
        text = self._extract_text(data)
        return self._parse_json(text)

    @staticmethod
    def _extract_text(api_response: dict[str, Any]) -> str:
        try:
            candidates = api_response["candidates"]
            parts = candidates[0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                raise ValueError("empty model text")
            return text
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(f"Failed to parse Gemini response: {api_response}") from exc

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise GeminiError("Expected top-level JSON object from Gemini")
            return parsed
        except json.JSONDecodeError as exc:
            raise GeminiError(f"Invalid JSON from Gemini: {text[:300]}") from exc
