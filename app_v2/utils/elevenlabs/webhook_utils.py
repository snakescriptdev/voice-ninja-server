"""
webhook_utils.py
────────────────────────────────────────────────────────────────────────────────
Verification for ElevenLabs webhook deliveries (post-call transcription, etc).
"""

import hashlib
import hmac
import time

SIGNATURE_TOLERANCE_SECONDS = 30 * 60  # matches ElevenLabs' own tolerance window


def verify_elevenlabs_webhook_signature(raw_body: bytes, sig_header: str, secret: str) -> bool:
    """
    ElevenLabs signs webhooks as `t=<unix_ts>,v0=<hex_hmac>`, where the HMAC
    message is `f"{timestamp}.{raw_body}"` (HMAC-SHA256, keyed by the
    workspace webhook secret) — mirrors the `elevenlabs` SDK's own vendored
    `webhooks_custom.py:construct_event`, hand-rolled here since this
    codebase has no existing SDK client instance to hang it off.
    """
    if not secret or not sig_header:
        return False

    timestamp = None
    signature = None
    for part in sig_header.split(","):
        if part.startswith("t="):
            timestamp = part[2:]
        elif part.startswith("v0="):
            signature = part

    if not timestamp or not signature:
        return False

    try:
        if int(timestamp) < int(time.time()) - SIGNATURE_TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False

    message = f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8")
    expected = "v0=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
