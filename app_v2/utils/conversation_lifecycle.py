"""
Shared helpers for the conversation-row lifecycle, used by every call-handling
websocket flow (regular test-connection, widget widget, public API).

A row is created with call_status=in_progress the moment a call starts (so it
shows up in the conversations list immediately), then finalized in place once
the call ends and ElevenLabs metadata is available — instead of only ever
inserting a row after the call is over.
"""
import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
from app_v2.databases.models import ConversationsModel, CoinUsageSettingsModel, AgentModel, UnifiedAuthModel
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.utils.coin_utils import deduct_coins
from app_v2.utils.cost_utils import (
    compute_live_charge_credits,
    estimate_costs_credits,
    compute_actual_breakdown,
)
from app_v2.utils.email_service import send_cost_overrun_email

logger = setup_logger(__name__)


def _dispatch_coro(coro) -> None:
    """Fire-and-forget an async coroutine from sync code, whether or not an
    event loop is already running in the current thread (finalize runs inside
    the websocket handler's loop, but occasionally off-thread)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        loop.create_task(coro)
    else:
        def _run():
            try:
                asyncio.run(coro)
            except Exception:
                logger.exception("Failed to send cost-overrun email")
        threading.Thread(target=_run, daemon=True).start()


def _maybe_alert_cost_overrun(record: ConversationsModel) -> None:
    """Email admins when a call's ACTUAL cost exceeded our CALCULATED estimate
    (conversation and/or LLM). Admin emails are resolved synchronously here so
    the dispatched coroutine never touches the DB session."""
    conv_over = (
        record.actual_conversation_credits is not None
        and record.calculated_conversation_cost is not None
        and record.actual_conversation_credits > record.calculated_conversation_cost
    )
    llm_over = (
        record.actual_llm_credits is not None
        and record.calculated_llm_cost is not None
        and record.actual_llm_credits > record.calculated_llm_cost
    )
    if not (conv_over or llm_over):
        return
    try:
        admin_emails = [
            a.email
            for a in db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.is_admin == True).all()
            if a.email
        ]
        if not admin_emails:
            return
        agent_name = (
            db.session.query(AgentModel.agent_name).filter(AgentModel.id == record.agent_id).scalar()
        )
        _dispatch_coro(
            send_cost_overrun_email(
                recipients=admin_emails,
                conversation_id=record.id,
                agent_name=agent_name,
                actual_conversation=record.actual_conversation_credits,
                calculated_conversation=record.calculated_conversation_cost,
                actual_llm=record.actual_llm_credits,
                calculated_llm=record.calculated_llm_cost,
            )
        )
    except Exception:
        logger.exception("Failed to evaluate/send cost-overrun alert")

# Error message set on a conversation when a call is cut short mid-call because
# the user ran out of coins. Shared with the websocket routers so the marker and
# the /details error_message stay consistent and filterable.
LOW_BALANCE_ERROR_MESSAGE = "Call ended due to low coins balance"


def calculate_conversation_cost(raw_el_cost: float) -> int:
    """
    Converts ElevenLabs' raw cost into coin cost: the actual bill is always
    raw_el_cost plus a configurable markup so we never charge the user less
    than ElevenLabs charged us. Must be called inside db().
    """
    settings = CoinUsageSettingsModel.get_settings()
    return int(raw_el_cost * (1 + settings.markup_percentage / 100))


def get_minimum_call_balance() -> int:
    """
    Minimum coin balance required to start a call: enough for the
    admin-configured minimum_call_minutes at the admin-configured safety
    estimate. Must be called inside db().
    """
    settings = CoinUsageSettingsModel.get_settings()
    return int(settings.minimum_call_minutes * settings.minimum_credits_per_minute)


def estimate_coins_used_so_far(
    call_start_time: datetime,
    agent_llm_price_per_minute: Optional[float] = None,
) -> int:
    """
    Estimates coins the in-progress call would be billed SO FAR — the mid-call
    stand-in for the real cost, which ElevenLabs only reports after the call
    ends. Combines the admin's conservative conversation rate with the agent's
    per-minute LLM price (USD floor) and applies the markup, so it errs high on
    purpose (cut the call before uncollectible debt). Telephony is 0 for now.

    agent_llm_price_per_minute: the agent's stored llm_price_per_minute; when
    None (unknown), only the conversation estimate is used — backward
    compatible with callers that don't have it. Must be called inside db().
    """
    settings = CoinUsageSettingsModel.get_settings()
    elapsed_minutes = (datetime.now(timezone.utc) - call_start_time).total_seconds() / 60
    return int(compute_live_charge_credits(agent_llm_price_per_minute, elapsed_minutes, settings))


def is_balance_exhausted(
    call_start_time: datetime,
    user_balance: int,
    agent_llm_price_per_minute: Optional[float] = None,
) -> bool:
    """
    Returns True once the estimated (LLM + conversation + telephony, with
    markup) cost of the in-progress call reaches the user's balance at call
    start, so callers can cut the call short instead of letting it run up an
    uncollectible debt. Must be called inside db().
    """
    return estimate_coins_used_so_far(call_start_time, agent_llm_price_per_minute) >= user_balance


def start_conversation(user_id: int, agent_id: int, channel: ChannelEnum) -> int:
    """Inserts the in_progress placeholder row. Must be called inside db()."""
    record = ConversationsModel(
        agent_id=agent_id,
        user_id=user_id,
        call_status=CallStatusEnum.in_progress,
        channel=channel,
    )
    db.session.add(record)
    db.session.commit()
    db.session.refresh(record)
    return record.id


def finalize_conversation(
    conversation_row_id: int,
    metadata: dict,
    elevenlabs_conv_id: str,
    reference_type: str = "conversation",
    error_message: Optional[str] = None,
) -> ConversationsModel:
    """
    Fills in the final outcome on the row created by start_conversation() and
    deducts coins for the call cost. Must be called inside db().

    force=True is passed to deduct_coins so that if the call cost exceeded the
    user's balance (overdraft), the full cost is still recorded and the
    balance goes negative rather than silently skipping the deduction.

    error_message: set when the call was cut short for a known reason (e.g.
    the monthly minutes limit was hit mid-call) even though ElevenLabs still
    returned real metadata. The transcript/duration/elevenlabs_conv_id are
    still saved (so conversation history keeps showing on the frontend), but
    call_status is forced to failed and error_message records why — instead
    of trusting ElevenLabs' own call_successful flag for that call.
    """
    record = db.session.query(ConversationsModel).get(conversation_row_id)
    if record is None:
        raise ValueError(f"Conversation row {conversation_row_id} not found")

    raw_cost = float(metadata.get("cost") or 0)
    calculated_cost = calculate_conversation_cost(raw_cost)

    record.message_count = metadata.get("message_count")
    record.duration = metadata.get("duration")
    record.transcript_summary = metadata.get("transcript_summary")
    record.elevenlabs_conv_id = elevenlabs_conv_id
    record.cost = raw_cost
    if error_message:
        record.call_status = CallStatusEnum.failed
        record.error_message = error_message
        record.ended_due_to_low_balance = (error_message == LOW_BALANCE_ERROR_MESSAGE)
    else:
        record.call_status = CallStatusEnum.success if metadata.get("call_successful") else CallStatusEnum.failed

    # ---- Cost audit: store the calculated estimate, the real ElevenLabs
    # breakdown, coins charged, and the resulting profit margin. ----
    settings = CoinUsageSettingsModel.get_settings()
    agent_llm_price = (
        db.session.query(AgentModel.llm_price_per_minute)
        .filter(AgentModel.id == record.agent_id)
        .scalar()
    )

    calculated = estimate_costs_credits(agent_llm_price, record.duration, settings)
    record.calculated_llm_cost = calculated["calculated_llm_cost"]
    record.calculated_conversation_cost = calculated["calculated_conversation_cost"]
    record.calculated_telephony_cost = calculated["calculated_telephony_cost"]

    record.coins_charged_to_user = calculated_cost
    actual = compute_actual_breakdown(
        total_elevenlabs_credits=raw_cost,
        llm_credits=metadata.get("llm_credits"),
        coins_charged_to_user=calculated_cost,
        settings=settings,
    )
    record.actual_llm_credits = actual["actual_llm_credits"]
    record.actual_conversation_credits = actual["actual_conversation_credits"]
    record.profit_percentage = actual["profit_percentage"]

    db.session.flush()

    if calculated_cost > 0:
        deduct_coins(
            user_id=record.user_id,
            amount=calculated_cost,
            reference_type=reference_type,
            reference_id=record.id,
            commit=False,
            force=True,
        )

    db.session.commit()
    db.session.refresh(record)

    # Alert admins if the real cost exceeded our estimate for this call.
    _maybe_alert_cost_overrun(record)

    return record


def mark_conversation_failed(conversation_row_id: Optional[int], error_message: Optional[str] = None) -> None:
    """
    Marks a placeholder row as failed when the call never produced retrievable
    metadata (e.g. crashed before/while connecting to ElevenLabs), so it
    doesn't stay stuck as "in progress" forever. Must be called inside db().
    """
    if not conversation_row_id:
        return
    record = db.session.query(ConversationsModel).get(conversation_row_id)
    if record is None:
        return
    record.call_status = CallStatusEnum.failed
    if error_message:
        record.error_message = error_message
    db.session.commit()
