"""
Shared helpers for the conversation-row lifecycle, used by every call-handling
websocket flow (regular test-connection, widget widget, public API).

A row is created with call_status=in_progress the moment a call starts (so it
shows up in the conversations list immediately), then finalized in place once
the call ends and ElevenLabs metadata is available — instead of only ever
inserting a row after the call is over.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
from app_v2.databases.models import ConversationsModel, CoinUsageSettingsModel
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.utils.coin_utils import deduct_coins

logger = setup_logger(__name__)


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
    return int(settings.minimum_call_minutes * settings.estimated_coins_per_minute)


def estimate_coins_used_so_far(call_start_time: datetime) -> int:
    """
    Estimates coins consumed by an in-progress call using the admin-configured
    per-minute safety rate — a stand-in for the real cost, which ElevenLabs
    only reports after the call ends. Must be called inside db().
    """
    settings = CoinUsageSettingsModel.get_settings()
    elapsed_minutes = (datetime.now(timezone.utc) - call_start_time).total_seconds() / 60
    return int(elapsed_minutes * settings.estimated_coins_per_minute)


def is_balance_exhausted(call_start_time: datetime, user_balance: int) -> bool:
    """
    Returns True once the estimated cost of the in-progress call reaches the
    user's balance at call start, so callers can cut the call short instead
    of letting it run up an uncollectible debt. Must be called inside db().
    """
    return estimate_coins_used_so_far(call_start_time) >= user_balance


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
    else:
        record.call_status = CallStatusEnum.success if metadata.get("call_successful") else CallStatusEnum.failed
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
