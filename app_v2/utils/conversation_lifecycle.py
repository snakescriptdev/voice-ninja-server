"""
Shared helpers for the conversation-row lifecycle, used by every call-handling
websocket flow (regular test-connection, web-agent widget, public API).

A row is created with call_status=in_progress the moment a call starts (so it
shows up in the conversations list immediately), then finalized in place once
the call ends and ElevenLabs metadata is available — instead of only ever
inserting a row after the call is over.
"""
from typing import Optional

from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
from app_v2.databases.models import ConversationsModel, CoinUsageSettingsModel
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.utils.coin_utils import deduct_coins

logger = setup_logger(__name__)


def calculate_conversation_cost(raw_el_cost: float) -> int:
    """Converts ElevenLabs' raw cost into coin cost. Must be called inside db()."""
    settings = CoinUsageSettingsModel.get_settings()
    return int((raw_el_cost * settings.elevenlabs_multiplier) + settings.static_conversation_cost)


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
) -> ConversationsModel:
    """
    Fills in the final outcome on the row created by start_conversation() and
    deducts coins for the call cost. Must be called inside db().

    force=True is passed to deduct_coins so that if the call cost exceeded the
    user's balance (overdraft), the full cost is still recorded and the
    balance goes negative rather than silently skipping the deduction.
    """
    record = db.session.query(ConversationsModel).get(conversation_row_id)
    if record is None:
        raise ValueError(f"Conversation row {conversation_row_id} not found")

    raw_cost = float(metadata.get("cost") or 0)
    calculated_cost = calculate_conversation_cost(raw_cost)

    record.message_count = metadata.get("message_count")
    record.duration = metadata.get("duration")
    record.call_status = CallStatusEnum.success if metadata.get("call_successful") else CallStatusEnum.failed
    record.transcript_summary = metadata.get("transcript_summary")
    record.elevenlabs_conv_id = elevenlabs_conv_id
    record.cost = raw_cost
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
