"""
Redaction/truncation helpers for anything persisted into APICallLogModel's
request_params/request_body/response_body columns. Keeps secrets out of the
Logs page and bounds row size regardless of what a caller sends/returns.
"""
import json
from typing import Any, Mapping, Optional

SENSITIVE_KEYS = {
    "authorization", "x-api-client-secret", "client_secret", "password",
    "secret", "token", "api_key", "access_token", "refresh_token",
    "card_number", "cvv", "cvc", "otp",
}

REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS


def redact(data: Any) -> Any:
    """Recursively redact any dict value whose key matches SENSITIVE_KEYS."""
    if isinstance(data, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive_key(k) else redact(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data


def sanitize_headers(headers: Mapping) -> dict:
    """Redact a Headers/dict-like mapping of request/response headers."""
    return {k: (REDACTED if _is_sensitive_key(k) else v) for k, v in dict(headers).items()}


def sanitize_for_log(data: Any, max_bytes: int = 10_000) -> Optional[dict]:
    """
    Redact sensitive keys, then bound the payload's stored size.

    Returns None for None input. If the (redacted) data can't be serialized
    to JSON, returns an "_unloggable" marker instead of raising. If the
    serialized size exceeds max_bytes, returns a "_truncated" marker with a
    short preview rather than storing the full payload.
    """
    if data is None:
        return None

    redacted = redact(data)
    try:
        serialized = json.dumps(redacted, default=str)
    except (TypeError, ValueError):
        return {"_unloggable": True, "reason": "not JSON-serializable"}

    if len(serialized.encode("utf-8")) > max_bytes:
        return {
            "_truncated": True,
            "size": len(serialized.encode("utf-8")),
            "preview": serialized[:1000],
        }

    return redacted
