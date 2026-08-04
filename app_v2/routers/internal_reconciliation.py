"""
Internal-only endpoint that reconciles conversation rows stuck in
`in_progress` because the process handling their call (test-connection
socket, widget/web-agent socket, or public API socket) crashed, was killed,
or the server restarted before the call could be finalized.

Not part of the public API — meant to be hit periodically by an external
crontab (see scripts/cron/reconcile_stuck_calls.py), which just does an
authenticated HTTP call here. All the actual reconciliation work (cost
calculation, coin deduction, cost-audit fields, and the admin/user alert
emails) is done by calling the SAME finalize_conversation() /
maybe_send_notifications() functions the live call flows already use —
there is no separate, independently-maintained copy of the billing math.
This is the whole point of this being a real API endpoint inside the app
rather than logic baked into the crontab script itself.

A row only gets touched once ElevenLabs itself reports the conversation has
actually ended (status "done"/"failed") — a call that's still genuinely in
progress, or whose analysis ElevenLabs is still assembling ("processing"),
is left alone and picked up on a later run.

Known limitation (pre-existing, not introduced by this endpoint): for
widget/web-agent calls, the specific widget (public_id) and lead used only
ever lived in memory during the live socket session. If that process
crashed, there is no way to recover which widget/lead it was, so the
reconciled "new conversation" owner email is attributed to the agent's name
instead of the exact widget, and any lead captured mid-call is left
unlinked.
"""
import asyncio
import secrets
import traceback
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Request, HTTPException
from fastapi_sqlalchemy import db

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.databases.models import ConversationsModel, AgentModel
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
from app_v2.utils.conversation_lifecycle import finalize_conversation, mark_conversation_failed
from app_v2.routers.widget import maybe_send_notifications

logger = setup_logger(__name__)

router = APIRouter(prefix="/api/v2/internal", tags=["internal"])

# Rows younger than this are left alone even if elevenlabs_conv_id is set —
# could still be a genuinely active call, not a crashed one.
STALE_AFTER_MINUTES = 10

# ElevenLabs conversation.status values (GET /v1/convai/conversations/{id}):
# https://elevenlabs.io/docs/api-reference/conversations/get
STILL_LIVE_STATUSES = {"initiated", "in-progress"}
ENDED_STATUSES = {"done", "failed"}

# extract_conversation_metadata()'s own defaults (max_retries=10,
# delay_seconds=4.0) assume it's called right as a live call just ended,
# when ElevenLabs may still be assembling analysis — worst case ~40s of
# blocking retry per row. Here we already know el_status is "done"/"failed"
# (never "processing", which is EL's own signal for "ended but still
# assembling"), so metadata is expected to be ready on the first attempt;
# these tighter bounds are just a safety margin, not the expected path.
# Keeps a single reconciliation run fast and predictable regardless of how
# many stuck rows it processes.
METADATA_MAX_RETRIES = 2
METADATA_RETRY_DELAY_SECONDS = 2.0


def _require_internal_auth(http_request: Request) -> None:
    """
    Same convention as personal_knowledge_base.py's internal webhook guard —
    requires `Authorization: Bearer <INTERNAL_API_SECRET_KEY>`, rejected with
    a constant-time comparison so response timing can't leak how much of the
    key was guessed correctly.
    """
    expected_secret = VoiceSettings.INTERNAL_API_SECRET_KEY
    if not expected_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    auth_header = http_request.headers.get("authorization") or ""
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not secrets.compare_digest(token, expected_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/reconcile-stuck-calls")
async def reconcile_stuck_calls(http_request: Request, max_rows: int = 2):
    """
    Finds `in_progress` rows with a captured elevenlabs_conv_id older than
    STALE_AFTER_MINUTES, checks each one's real status on ElevenLabs, and
    finalizes (or fails) any whose call has actually ended.
    """
    _require_internal_auth(http_request)

    with db():
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_AFTER_MINUTES)
        stuck = (
            db.session.query(ConversationsModel)
            .filter(
                ConversationsModel.call_status == CallStatusEnum.in_progress,
                ConversationsModel.elevenlabs_conv_id.isnot(None),
                ConversationsModel.created_at < cutoff,
            )
            .order_by(ConversationsModel.created_at.asc())
            .limit(max_rows)
            .all()
        )
        # Snapshot the handful of fields we need — the rows themselves get
        # detached the moment this `with db():` block closes.
        rows = [(r.id, r.elevenlabs_conv_id, r.channel, r.agent_id) for r in stuck]

    summary = {
        "checked": len(rows),
        "finalized": 0,
        "still_in_progress": 0,
        "still_processing": 0,
        "already_claimed": 0,
        "errors": [],
    }

    el_conv = ElevenLabsConversation()

    for conversation_row_id, conv_id, channel, agent_id in rows:
        try:
            response = await asyncio.to_thread(el_conv.get_conversation, conv_id)
        except Exception as e:
            logger.error(f"reconcile: status check failed for row={conversation_row_id}: {e}")
            summary["errors"].append({"conversation_row_id": conversation_row_id, "stage": "status_check", "error": str(e)})
            continue

        if not response.status or not response.data:
            summary["errors"].append({
                "conversation_row_id": conversation_row_id, "stage": "status_check",
                "error": response.error_message or "empty response",
            })
            continue

        el_status = response.data.get("status")
        if el_status in STILL_LIVE_STATUSES:
            summary["still_in_progress"] += 1
            continue
        if el_status == "processing":
            summary["still_processing"] += 1
            continue
        if el_status not in ENDED_STATUSES:
            # Unrecognized status — be conservative and leave it alone
            # rather than guessing whether the call actually ended.
            logger.warning(f"reconcile: unexpected EL status '{el_status}' for row={conversation_row_id}")
            summary["still_in_progress"] += 1
            continue

        # el_status is "done" or "failed" — the call genuinely ended.
        try:
            # Atomically claim this row before doing the slow EL-metadata
            # fetch + finalize work below. Postgres serializes concurrent
            # UPDATEs to the same row, so if two overlapping reconciliation
            # runs both reach this point for the same row, the second one's
            # UPDATE blocks until the first commits, then re-evaluates its
            # WHERE clause against the now-committed state — error_message
            # IS NULL is required (not just call_status) precisely so that
            # re-evaluation fails for the second run (the first already set
            # it to a non-null marker), giving rowcount 0. Without the
            # error_message check here, both runs would still match on
            # call_status='in_progress' alone (unchanged by the claim
            # itself) and both would proceed to finalize — double-charging
            # the user, which is exactly what happened before this guard
            # existed.
            def _claim():
                with db():
                    updated = (
                        db.session.query(ConversationsModel)
                        .filter(
                            ConversationsModel.id == conversation_row_id,
                            ConversationsModel.call_status == CallStatusEnum.in_progress,
                            ConversationsModel.error_message.is_(None),
                        )
                        .update({"error_message": "Reconciliation: claimed"}, synchronize_session=False)
                    )
                    db.session.commit()
                    return updated

            claimed_rows = await asyncio.to_thread(_claim)
            if claimed_rows == 0:
                logger.info(f"reconcile: row={conversation_row_id} already claimed/finalized elsewhere, skipping")
                summary["already_claimed"] += 1
                continue

            metadata = await asyncio.to_thread(
                el_conv.extract_conversation_metadata, conv_id, METADATA_MAX_RETRIES, METADATA_RETRY_DELAY_SECONDS,
            )
            if not metadata:
                with db():
                    mark_conversation_failed(conversation_row_id, "Reconciliation: metadata extraction failed")
                summary["errors"].append({"conversation_row_id": conversation_row_id, "stage": "metadata", "error": "empty metadata"})
                continue

            def _finalize():
                with db():
                    # reference_type="conversation" (the default) — matching
                    # every live call flow — is required for the admin/user
                    # dashboards' "Charged" columns to find this deduction:
                    # they look up coins_ledger filtered by an exact
                    # reference_type=="conversation" match (see
                    # admin_dashboard.py), so any other string here silently
                    # displays as 0 charged even though the coins were
                    # correctly deducted.
                    record = finalize_conversation(conversation_row_id, metadata, conv_id)
                    # finalize_conversation() only ever WRITES error_message
                    # when its own error_message param is truthy (which we
                    # never pass) — on the success path it leaves whatever
                    # was already there untouched, so our claim marker from
                    # _claim() above would otherwise survive on an
                    # otherwise-healthy, successfully-billed conversation.
                    if record.error_message == "Reconciliation: claimed":
                        record.error_message = None
                        db.session.commit()
                    agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
                    agent_name = agent.agent_name if agent else "Agent"
                    db.session.refresh(record)
                    return record, agent_name

            record, agent_name = await asyncio.to_thread(_finalize)

            summary["finalized"] += 1
            logger.info(f"reconcile: finalized row={conversation_row_id} conv_id={conv_id}")

            if channel in (ChannelEnum.widget, ChannelEnum.web_agent):
                # The specific widget/lead used for this call only ever
                # lived in-memory during the live session and is lost once
                # the process crashed, so the owner notification is
                # attributed to the agent's name rather than the exact
                # widget, and the lead (if any) is left unlinked.
                fake_ctx = SimpleNamespace(user_id=record.user_id, widget_name=agent_name)
                await maybe_send_notifications(fake_ctx, record, metadata, lead_id=None)

        except Exception:
            logger.error(f"reconcile: failed to finalize row={conversation_row_id}:\n{traceback.format_exc()}")
            with db():
                mark_conversation_failed(conversation_row_id, "Reconciliation: failed to save conversation")
            summary["errors"].append({"conversation_row_id": conversation_row_id, "stage": "finalize", "error": "see server logs"})

    return summary
