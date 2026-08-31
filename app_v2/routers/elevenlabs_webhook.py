"""
elevenlabs_webhook.py
────────────────────────────────────────────────────────────────────────────────
Temporary capture endpoint for ElevenLabs' post-call webhook.

Purpose right now: log the raw payload/headers a real post-call delivery
sends, so the actual field shape can be confirmed before the real handler
(idempotency, finalize dispatch) is built. Signature verification is real —
only the DB/finalize side is still a stub.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.utils.elevenlabs.webhook_utils import verify_elevenlabs_webhook_signature

logger = setup_logger(__name__)
router = APIRouter(prefix="/api/v2/webhooks", tags=["Webhooks"])

# Testing-only capture dir for inspecting real payload shapes before the real
# handler (idempotency, finalize dispatch) is built. Remove once that's done.
CAPTURE_DIR = Path(__file__).resolve().parent.parent.parent / "webhook_captures"


@router.post("/elevenlabs", status_code=status.HTTP_200_OK)
async def elevenlabs_webhook_capture(request: Request):
    raw_body: bytes = await request.body()
    sig_header = request.headers.get("ElevenLabs-Signature", "")

    if not verify_elevenlabs_webhook_signature(raw_body, sig_header, VoiceSettings.ELEVENLABS_WEBHOOK_SECRET):
        logger.warning("ElevenLabs webhook: signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
        pretty_payload = json.dumps(payload, indent=2)
    except json.JSONDecodeError:
        payload = None
        pretty_payload = raw_body.decode("utf-8", errors="replace")

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    capture_file = CAPTURE_DIR / f"elevenlabs_{timestamp}.json"
    capture_file.write_text(
        json.dumps({"headers": dict(request.headers), "body": payload if payload is not None else pretty_payload}, indent=2)
    )
    logger.info("ElevenLabs webhook received, captured to %s", capture_file)

    return {"status": "received"}
