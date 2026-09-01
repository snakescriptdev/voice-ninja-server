"""
elevenlabs_webhook.py
────────────────────────────────────────────────────────────────────────────────
ElevenLabs post-call webhook — replaces the old polling-retry that used to
block WS teardown (up to 10 retries x 4s against GET /conversations/{id})
right after every call. Each live flow now just records that the call ended
(see mark_call_ended_pending_webhook in conversation_lifecycle.py); this
endpoint does the metadata-driven finalize once ElevenLabs' async analysis
is ready, dispatching to the same per-channel finalize logic the live flows
used to run inline.

Always returns 200 (even on internal errors) so ElevenLabs doesn't
retry-storm a failing delivery — failures are logged to WebhookEventLogModel
and leave the row for the manual "reload" retry button to pick up.
"""

import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi_sqlalchemy import db

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.databases.models import ConversationsModel, WebhookEventLogModel
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.utils.conversation_lifecycle import claim_conversation_for_finalize, mark_conversation_failed
from app_v2.utils.elevenlabs.conversation_utils import build_metadata_from_conv_data
from app_v2.utils.elevenlabs.webhook_utils import verify_elevenlabs_webhook_signature
from app_v2.utils.log_sanitizer import sanitize_for_log
from app_v2.routers.public_websocket_router import finalize_public_conversation, mark_public_conversation_failed
from app_v2.routers.widget import finalize_web_conversation_and_notify, mark_web_conversation_failed
from app_v2.routers.websocket_router import finalize_test_connection_and_alert

logger = setup_logger(__name__)
router = APIRouter(prefix="/api/v2/webhooks", tags=["Webhooks"])


def _run_in_db(fn):
    """Runs a zero-arg callable inside a db() session context, off the event loop."""
    with db():
        return fn()


def _mark_failed_for_channel(channel: Optional[ChannelEnum], conversation_row_id: int, ws_log_id: Optional[int], reason: str) -> None:
    """Dispatches to the right failure path per channel. Must be called inside db()."""
    if channel == ChannelEnum.api:
        mark_public_conversation_failed(conversation_row_id, ws_log_id, reason)
    elif channel in (ChannelEnum.widget, ChannelEnum.web_agent):
        mark_web_conversation_failed(conversation_row_id, ws_log_id, reason)
    else:
        mark_conversation_failed(conversation_row_id, reason)


def _log_webhook_event(conversation_id: str, event_type: str, data: dict, status_str: str, error: Optional[str] = None) -> None:
    """
    Idempotency + audit log for this delivery. Must be called inside db().

    The raw payload's transcript can carry tool_calls[].tool_details.headers
    with our own internal Authorization bearer secret (e.g. for the
    personal-KB search webhook tool) — sanitize_for_log() strips that before
    it's ever persisted here, same as every other audit-log write in this
    codebase. A much higher max_bytes than the default is used since a full
    conversation payload with per-turn metrics regularly runs 30KB+.
    """
    log = WebhookEventLogModel(
        provider="elevenlabs",
        event_id=conversation_id,
        event_type=event_type,
        payload=sanitize_for_log(data, max_bytes=500_000),
        status=status_str,
        error_message=error,
        processed_at=datetime.now(timezone.utc),
    )
    db.session.add(log)
    db.session.commit()


def _claim_row(conversation_id: str) -> dict:
    """
    Looks up the row by elevenlabs_conv_id and atomically claims it for
    finalize. Returns a plain dict (not the ORM object) since the session
    closes when this returns. Must be called inside db().
    """
    already_processed = (
        db.session.query(WebhookEventLogModel)
        .filter(WebhookEventLogModel.event_id == conversation_id, WebhookEventLogModel.status == "processed")
        .first()
    )
    if already_processed:
        return {"outcome": "duplicate"}

    record = (
        db.session.query(ConversationsModel)
        .filter(ConversationsModel.elevenlabs_conv_id == conversation_id)
        .first()
    )
    if record is None:
        return {"outcome": "no_matching_row"}

    if record.call_status != CallStatusEnum.in_progress:
        return {"outcome": "already_finalized"}

    if not claim_conversation_for_finalize(record.id):
        return {"outcome": "already_claimed"}

    return {
        "outcome": "claimed",
        "conversation_row_id": record.id,
        "channel": record.channel,
        "user_id": record.user_id,
        "pending_context": record.pending_finalize_context or {},
    }


@router.post("/elevenlabs", status_code=status.HTTP_200_OK)
async def elevenlabs_webhook(request: Request):
    raw_body: bytes = await request.body()
    sig_header = request.headers.get("ElevenLabs-Signature", "")

    if not verify_elevenlabs_webhook_signature(raw_body, sig_header, VoiceSettings.ELEVENLABS_WEBHOOK_SECRET):
        logger.warning("ElevenLabs webhook: signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error("ElevenLabs webhook: invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("type", "")
    if event_type != "post_call_transcription":
        logger.info("ElevenLabs webhook: unhandled event type '%s' - ignoring", event_type)
        return {"status": "ignored"}

    data = payload.get("data") or {}
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        logger.warning("ElevenLabs webhook: payload missing data.conversation_id")
        return {"status": "ignored"}

    # ElevenLabs only fires this webhook once a conversation's analysis is
    # complete, but a status other than "done" (if it can ever occur here)
    # means there's nothing finalizable yet.
    if data.get("status") != "done":
        logger.info("ElevenLabs webhook: conversation %s status=%s, not finalizing", conversation_id, data.get("status"))
        return {"status": "ignored"}

    prep = await asyncio.to_thread(lambda: _run_in_db(lambda: _claim_row(conversation_id)))
    outcome = prep["outcome"]
    if outcome != "claimed":
        logger.info("ElevenLabs webhook: conversation %s -> %s", conversation_id, outcome)
        return {"status": outcome}

    conversation_row_id = prep["conversation_row_id"]
    channel = prep["channel"]
    user_id = prep["user_id"]
    pending_context = prep["pending_context"]
    error_message = pending_context.get("limit_error")
    ws_log_id = pending_context.get("ws_log_id")

    metadata = build_metadata_from_conv_data(data)

    if not metadata:
        reason = error_message or "Metadata missing from webhook payload"
        logger.error("ElevenLabs webhook: incomplete metadata for conversation %s despite status=done", conversation_id)
        await asyncio.to_thread(lambda: _run_in_db(lambda: _mark_failed_for_channel(channel, conversation_row_id, ws_log_id, reason)))
        await asyncio.to_thread(lambda: _run_in_db(lambda: _log_webhook_event(conversation_id, event_type, data, "failed", reason)))
        return {"status": "metadata_incomplete"}

    try:
        if channel == ChannelEnum.api:
            await asyncio.to_thread(
                lambda: _run_in_db(
                    lambda: finalize_public_conversation(conversation_row_id, metadata, conversation_id, ws_log_id, error_message=error_message)
                )
            )
        elif channel in (ChannelEnum.widget, ChannelEnum.web_agent):
            await finalize_web_conversation_and_notify(
                conversation_row_id, metadata, conversation_id,
                pending_context.get("lead_id"), ws_log_id,
                pending_context.get("widget_name") or "",
                error_message=error_message,
            )
        elif channel == ChannelEnum.test_voice:
            await finalize_test_connection_and_alert(
                user_id, conversation_row_id, metadata, conversation_id, error_message=error_message,
            )
        else:
            logger.error("ElevenLabs webhook: unknown channel %s for conversation_row_id %s", channel, conversation_row_id)
            await asyncio.to_thread(lambda: _run_in_db(lambda: mark_conversation_failed(conversation_row_id, "Unknown channel")))

        await asyncio.to_thread(lambda: _run_in_db(lambda: _log_webhook_event(conversation_id, event_type, data, "processed")))
        return {"status": "processed"}

    except Exception:
        logger.error("ElevenLabs webhook: finalize failed for %s:\n%s", conversation_id, traceback.format_exc())
        reason = error_message or "Failed to save conversation"
        await asyncio.to_thread(lambda: _run_in_db(lambda: _mark_failed_for_channel(channel, conversation_row_id, ws_log_id, reason)))
        await asyncio.to_thread(lambda: _run_in_db(lambda: _log_webhook_event(conversation_id, event_type, data, "failed", reason)))
        return {"status": "error"}
