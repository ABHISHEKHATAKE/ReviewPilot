from __future__ import annotations

import json
from typing import Any

import httpx

from backend.config import get_settings


class GroqServiceError(RuntimeError):
    pass


class GroqClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.settings.groq_api_key:
            raise GroqServiceError("GROQ_API_KEY is not configured.")

        url = f"{self.settings.groq_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.groq_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.settings.groq_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise GroqServiceError(
                f"Groq API returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GroqServiceError(f"Could not connect to the Groq API: {exc}") from exc

        try:
            content = response.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise GroqServiceError("Groq returned an invalid JSON response.") from exc

        if not isinstance(result, dict):
            raise GroqServiceError("Groq JSON response must be an object.")
        return result
