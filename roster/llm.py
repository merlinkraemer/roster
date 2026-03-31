import os
import sys

import requests

from .config import load_api_key

_DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_DEFAULT_MODEL = "glm-4.5-turbo"


def _api_key() -> str:
    key = load_api_key()
    if not key:
        sys.exit(
            "Error: no API key found.\n"
            "Run `roster auth` to save your ZAI_API_KEY, "
            "or set the ZAI_API_KEY environment variable."
        )
    return key


def call_llm(system: str, user: str) -> str:
    model = os.environ.get("ROSTER_MODEL", _DEFAULT_MODEL)
    base_url = os.environ.get("ROSTER_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/chat/completions"

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]
