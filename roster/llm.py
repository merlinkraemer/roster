import os

import requests

from .config import load_api_key

_DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_DEFAULT_MODEL = "glm-5-turbo"


def _api_key() -> str:
    key = load_api_key()
    if not key:
        raise SystemExit(
            "Error: no API key found.\n"
            "Run `roster auth` to save your ZAI_API_KEY, "
            "or set the ZAI_API_KEY environment variable."
        )
    return key


class APIError(Exception):
    """Raised when an API call fails with a user-friendly message."""

    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint
        super().__init__(message)


def call_llm(system: str, user: str, timeout: int = 300) -> str:
    model = os.environ.get("ROSTER_MODEL", _DEFAULT_MODEL)
    base_url = os.environ.get("ROSTER_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/chat/completions"

    try:
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
                "thinking": {"type": "enabled", "clear_thinking": False},
            },
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.Timeout:
        raise APIError(
            "API request timed out",
            hint="The plan may be too large. Try splitting it into smaller files, or set ROSTER_TIMEOUT env var.",
        )
    except requests.ConnectionError:
        raise APIError(
            "Could not connect to API",
            hint="Check your internet connection or try again later.",
        )
    except requests.HTTPError as e:
        if e.response is not None:
            if e.response.status_code == 401:
                raise APIError(
                    "API key is invalid or expired",
                    hint="Run 'roster auth' to update your key.",
                )
            if e.response.status_code == 429:
                raise APIError(
                    "Rate limited by the API",
                    hint="Wait a moment and try again.",
                )
            if e.response.status_code >= 500:
                raise APIError(
                    f"API server error ({e.response.status_code})",
                    hint="The API may be temporarily down. Try again later.",
                )
            detail = ""
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            raise APIError(
                f"API request failed ({e.response.status_code})",
                hint=detail or "Check your API key and try again.",
            )
        raise APIError("API request failed", hint=str(e))

    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise APIError(
            "Unexpected API response format",
            hint="The API may have changed. Try again.",
        )


def test_api_key(key: str | None = None) -> dict:
    """Ping the Z.AI coding endpoint with a test message. Returns result dict."""
    key = key or _api_key()
    base_url = os.environ.get("ROSTER_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("ROSTER_MODEL", _DEFAULT_MODEL)
    url = f"{base_url}/chat/completions"

    test_prompt = "Reply with exactly: ok"
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept-Language": "en-US,en",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": test_prompt},
                ],
                "thinking": {"type": "enabled", "clear_thinking": False},
            },
            timeout=30,
        )
    except requests.RequestException as e:
        return {
            "ok": False,
            "error": f"connection: {e}",
            "model": model,
            "endpoint": url,
            "prompt": test_prompt,
        }

    if response.status_code == 401:
        return {
            "ok": False,
            "error": "unauthorized",
            "model": model,
            "endpoint": url,
            "prompt": test_prompt,
        }
    if response.status_code == 403:
        return {
            "ok": False,
            "error": "forbidden",
            "model": model,
            "endpoint": url,
            "prompt": test_prompt,
        }
    if response.status_code == 429:
        return {
            "ok": False,
            "error": "rate_limited",
            "model": model,
            "endpoint": url,
            "prompt": test_prompt,
        }
    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        return {
            "ok": False,
            "error": f"{response.status_code} {detail}".strip(),
            "model": model,
            "endpoint": url,
            "prompt": test_prompt,
        }

    try:
        data = response.json()
        reply = data["choices"][0]["message"]["content"]
        model_used = data.get("model", model)
        usage = data.get("usage", {})
    except (KeyError, IndexError) as e:
        return {
            "ok": False,
            "error": f"unexpected response: {e}",
            "model": model,
            "endpoint": url,
            "prompt": test_prompt,
        }

    return {
        "ok": True,
        "model": model_used,
        "endpoint": url,
        "prompt": test_prompt,
        "response": reply.strip(),
        "usage": usage,
    }
