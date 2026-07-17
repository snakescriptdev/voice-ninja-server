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
from sqlalchemy import func

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.databases.models import (
    ConversationsModel,
    CoinUsageSettingsModel,
    CoinUsageSettingsVersionModel,
    AgentModel,
    UnifiedAuthModel,
)
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.utils.coin_utils import deduct_coins, get_user_coin_balance
from app_v2.utils.cost_utils import (
    compute_live_charge_credits,
    estimate_costs_credits,
    compute_actual_breakdown,
)
from app_v2.utils.email_service import send_cost_overrun_email, send_insufficient_call_balance_email

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


# Only alert admins when the actual cost exceeds our calculated estimate by
# more than this percentage — a small overage is noise, underruns (actual
# below calculated) are never anomalies worth flagging.
COST_OVERRUN_ALERT_THRESHOLD_PCT = 10.0


def _overrun_pct(actual: Optional[float], calculated: Optional[float]) -> Optional[float]:
    """
    Returns how much `actual` exceeds `calculated`, as a percentage (e.g. 15.0
    means actual is 15% higher). Returns None when actual is at or below
    calculated — underruns are never an anomaly, no matter how large — or
    when either value is missing. Returns inf when calculated is 0 but actual
    is positive (a cost appeared where none was estimated).
    """
    if actual is None or calculated is None or actual <= calculated:
        return None
    if calculated <= 0:
        return float("inf")
    return (actual - calculated) / calculated * 100.0


def _maybe_alert_cost_overrun(record: ConversationsModel) -> None:
    """Email admins when a call's ACTUAL cost exceeded our CALCULATED estimate
    (conversation and/or LLM) by more than COST_OVERRUN_ALERT_THRESHOLD_PCT —
    i.e. a margin loss significant enough to be worth reviewing, not routine
    variance. Admin emails are resolved synchronously here so the dispatched
    coroutine never touches the DB session."""
    conv_pct = _overrun_pct(record.actual_conversation_credits, record.calculated_conversation_cost)
    llm_pct = _overrun_pct(record.actual_llm_credits, record.calculated_llm_cost)

    conv_over = conv_pct is not None and conv_pct > COST_OVERRUN_ALERT_THRESHOLD_PCT
    llm_over = llm_pct is not None and llm_pct > COST_OVERRUN_ALERT_THRESHOLD_PCT
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
                conversation_overrun_pct=conv_pct if conv_over else None,
                actual_llm=record.actual_llm_credits,
                calculated_llm=record.calculated_llm_cost,
                llm_overrun_pct=llm_pct if llm_over else None,
            )
        )
    except Exception:
        logger.exception("Failed to evaluate/send cost-overrun alert")


def _maybe_alert_insufficient_call_balance(
    record: ConversationsModel,
    settings: CoinUsageSettingsModel,
) -> None:
    """
    Email the user when their post-call coin balance can no longer cover even
    the minimum admin-configured call (minimum_call_minutes at
    minimum_credits_per_minute), so they understand why new calls will now be
    refused. Gated on the same usage-alerts preference/email presence as the
    generic low-coins alert.
    """
    minimum_required = int(settings.minimum_call_minutes * settings.minimum_credits_per_minute)
    if minimum_required <= 0:
        return
    try:
        current_balance = get_user_coin_balance(record.user_id)
        if current_balance >= minimum_required:
            return

        user = db.session.query(UnifiedAuthModel).get(record.user_id)
        if not user or not user.email:
            return
        alerts_enabled = user.notification_settings and user.notification_settings.useage_alerts
        if not alerts_enabled:
            return

        minutes_available = max(current_balance, 0) / settings.minimum_credits_per_minute
        _dispatch_coro(
            send_insufficient_call_balance_email(
                user_email=user.email,
                user_name=user.first_name or user.name or "User",
                current_balance=current_balance,
                minimum_credits_per_minute=settings.minimum_credits_per_minute,
                minutes_available=minutes_available,
                base_url=VoiceSettings.FRONTEND_URL,
            )
        )
    except Exception:
        logger.exception("Failed to evaluate/send insufficient-call-balance alert")

# Error message set on a conversation when a call is cut short mid-call because
# the user ran out of coins. Shared with the websocket routers so the marker and
# the /details error_message stay consistent and filterable.
LOW_BALANCE_ERROR_MESSAGE = "Call ended due to low coins balance"

# Error message set on a conversation when an agent's very first call is cut
# short by the admin-configured first_call_max_duration_seconds safety cap.
FIRST_CALL_DURATION_LIMIT_ERROR_MESSAGE = "Call ended: first call duration limit reached"


def resolve_llm_cost_multiplier(agent: Optional[AgentModel], settings: CoinUsageSettingsModel) -> float:
    """
    Picks the higher of the admin-configured KB/tool LLM-cost multipliers when
    the agent has that feature attached — KB retrieval and custom tool
    round-trips both add LLM cost beyond ElevenLabs' bare per-minute price
    estimate, but the two overheads are assumed to overlap rather than stack,
    so the max (not the product) is used. "Has tools" only counts custom
    function tools (agent_functions), not built-in toggles (e.g. end-call).
    Falls back to 1.0 (no adjustment) when the agent has neither, or is None.
    Must be called inside db() — accesses agent relationships.
    """
    if agent is None:
        return 1.0
    applicable = [1.0]
    if agent.agent_knowledge_bases:
        applicable.append(float(settings.knowledge_base_llm_cost_multiplier or 1.0))
    if agent.agent_functions:
        applicable.append(float(settings.tool_llm_cost_multiplier or 1.0))
    return max(applicable)


def is_agents_first_call(agent_id: int) -> bool:
    """
    True if this agent has never had a prior conversation row. Must be called
    BEFORE start_conversation() inserts the placeholder row for the current
    call, so that row doesn't count against itself. Must be called inside
    db().
    """
    prior_count = (
        db.session.query(func.count(ConversationsModel.id))
        .filter(ConversationsModel.agent_id == agent_id)
        .scalar()
    )
    return not prior_count


def is_first_call_duration_exceeded(call_start_time: datetime, is_first_call: bool) -> bool:
    """
    True once an agent's FIRST-ever call has run past the admin-configured
    first_call_max_duration_seconds safety cap — a call length ceiling used
    while a freshly configured agent's LLM price / KB / tool multipliers are
    still unproven. Only applies to that one first call; every call after it
    is uncapped. A cap of 0 (the default) disables this check entirely.
    Must be called inside db().
    """
    if not is_first_call:
        return False
    settings = CoinUsageSettingsModel.get_settings()
    cap_seconds = settings.first_call_max_duration_seconds
    if not cap_seconds or cap_seconds <= 0:
        return False
    elapsed_seconds = (datetime.now(timezone.utc) - call_start_time).total_seconds()
    return elapsed_seconds >= cap_seconds


def resolve_llm_rate_basis(agent: Optional[AgentModel]) -> Optional[dict]:
    """
    Learns an LLM-cost-per-turn rate from the agent's own last completed call,
    to project the live mid-call LLM estimate from instead of the flat
    admin-configured multiplier (resolve_llm_cost_multiplier) — turn count
    tracks LLM cost far more precisely than a one-size-fits-all multiplier,
    and every agent's own history already reflects its actual system-prompt
    length, tool count, KB pages, and RAG usage.

    Returns {"turns_per_minute": float, "credits_per_turn": float}, or None
    (meaning: fall back to the flat multiplier) when:
    - agent is None,
    - there's no completed prior call with usable turn/duration/cost data, or
    - the agent's CURRENT config (system prompt length, tool count, KB pages,
      RAG flag) no longer matches what that last call ran under — the agent
      was edited since, so the learned rate is stale.

    Only ever used for the LIVE estimate; the post-call audit estimate
    (estimate_costs_credits, calculated_llm_cost) is intentionally left on
    the flat-multiplier formula so it keeps a stable, comparable meaning.
    Must be called inside db().
    """
    if agent is None:
        return None

    last_call = (
        db.session.query(ConversationsModel)
        .filter(
            ConversationsModel.agent_id == agent.id,
            ConversationsModel.call_status != CallStatusEnum.in_progress,
            ConversationsModel.duration.isnot(None),
            ConversationsModel.duration > 0,
            ConversationsModel.user_message_count.isnot(None),
            ConversationsModel.agent_message_count.isnot(None),
            ConversationsModel.actual_llm_credits.isnot(None),
        )
        .order_by(ConversationsModel.created_at.desc())
        .first()
    )
    if last_call is None:
        return None

    total_turns = (last_call.user_message_count or 0) + (last_call.agent_message_count or 0)
    if total_turns <= 0:
        return None

    current_system_prompt_length = len(agent.system_prompt) if agent.system_prompt else 0
    current_tool_count = len(agent.agent_functions)
    config_matches = (
        current_system_prompt_length == last_call.system_prompt_length
        and current_tool_count == last_call.tool_count
        and agent.kb_total_pages == last_call.kb_total_pages
        and agent.rag_enabled == last_call.rag_enabled
    )
    if not config_matches:
        return None

    return {
        "turns_per_minute": total_turns / (last_call.duration / 60.0),
        "credits_per_turn": last_call.actual_llm_credits / total_turns,
    }


def estimate_llm_credits_from_turns(elapsed_minutes: float, rate_basis: dict) -> float:
    """
    Projects LLM credits consumed SO FAR from a learned per-turn rate basis
    (see resolve_llm_rate_basis): estimated turns so far × credits per turn.
    """
    estimated_turns_so_far = elapsed_minutes * rate_basis["turns_per_minute"]
    return estimated_turns_so_far * rate_basis["credits_per_turn"]


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


# Billing-relevant CoinUsageSettingsModel fields — a change to ANY of these
# triggers a new CoinUsageSettingsVersionModel snapshot. credits_per_rupee /
# minimum_purchase_amount_inr are deliberately excluded: they govern
# purchasing, not what a conversation is charged.
SETTINGS_VERSION_FIELDS = [
    "elevenlabs_conversation_credits_per_minute",
    "usd_to_credits",
    "markup_percentage",
    "minimum_credits_per_minute",
    "minimum_call_minutes",
    "first_call_max_duration_seconds",
    "knowledge_base_llm_cost_multiplier",
    "tool_llm_cost_multiplier",
]


def _snapshot_settings_version(settings: CoinUsageSettingsModel, version_number: int) -> CoinUsageSettingsVersionModel:
    """Inserts an immutable snapshot of the current billing-relevant fields. Must be called inside db()."""
    version = CoinUsageSettingsVersionModel(
        version_number=version_number,
        **{field: getattr(settings, field) for field in SETTINGS_VERSION_FIELDS},
    )
    db.session.add(version)
    db.session.flush()
    return version


def get_or_create_current_settings_version(settings: CoinUsageSettingsModel) -> int:
    """
    Returns the id of the CoinUsageSettingsVersionModel snapshot currently in
    effect. Lazily creates version 1 from the live settings if none exists
    yet (e.g. an install from before this feature shipped). Must be called
    inside db().
    """
    if settings.current_version_id:
        return settings.current_version_id
    version = _snapshot_settings_version(settings, version_number=1)
    settings.current_version_id = version.id
    db.session.commit()
    return version.id


def maybe_create_new_settings_version(settings: CoinUsageSettingsModel, before: dict) -> None:
    """
    Call right after applying admin-submitted updates to `settings` (but
    before committing). If any billing-relevant field's value actually
    changed from `before` (a {field: old_value} dict captured prior to
    applying the update), snapshots a new version and repoints
    current_version_id at it — so every future conversation traces back to
    exactly the rates in effect when it was charged. A no-op if nothing
    billing-relevant changed. Must be called inside db().
    """
    changed = any(before[field] != getattr(settings, field) for field in SETTINGS_VERSION_FIELDS)
    if not changed:
        return
    last_version_number = (
        db.session.query(func.max(CoinUsageSettingsVersionModel.version_number)).scalar() or 0
    )
    version = _snapshot_settings_version(settings, version_number=last_version_number + 1)
    settings.current_version_id = version.id


def estimate_coins_used_so_far(
    call_start_time: datetime,
    agent_llm_price_per_minute: Optional[float] = None,
    llm_cost_multiplier: float = 1.0,
    llm_rate_basis: Optional[dict] = None,
) -> int:
    """
    Estimates coins the in-progress call would be billed SO FAR — the mid-call
    stand-in for the real cost, which ElevenLabs only reports after the call
    ends. Combines the admin's conservative conversation rate with the LLM
    cost estimate and applies the markup, so it errs high on purpose (cut the
    call before uncollectible debt). Telephony is 0 for now.

    agent_llm_price_per_minute: the agent's stored llm_price_per_minute; when
    None (unknown), only the conversation estimate is used — backward
    compatible with callers that don't have it. Ignored when llm_rate_basis
    is given.

    llm_cost_multiplier: see resolve_llm_cost_multiplier(); precompute once at
    call setup and pass through rather than re-resolving on every poll. Used
    as the fallback whenever llm_rate_basis is None.

    llm_rate_basis: see resolve_llm_rate_basis() — a per-agent, turns-based
    LLM rate learned from that agent's last completed call. When given (i.e.
    not the agent's first call, and a fresh matching-config prior call
    exists), this REPLACES the multiplier-based LLM estimate for this call.

    Must be called inside db().
    """
    settings = CoinUsageSettingsModel.get_settings()
    elapsed_minutes = (datetime.now(timezone.utc) - call_start_time).total_seconds() / 60
    llm_credits_override = (
        estimate_llm_credits_from_turns(elapsed_minutes, llm_rate_basis)
        if llm_rate_basis is not None
        else None
    )
    return int(compute_live_charge_credits(
        agent_llm_price_per_minute, elapsed_minutes, settings, llm_cost_multiplier,
        llm_credits_override=llm_credits_override,
    ))


def is_balance_exhausted(
    call_start_time: datetime,
    user_balance: int,
    agent_llm_price_per_minute: Optional[float] = None,
    llm_cost_multiplier: float = 1.0,
    llm_rate_basis: Optional[dict] = None,
) -> bool:
    """
    Returns True once the estimated (LLM + conversation + telephony, with
    markup) cost of the in-progress call reaches the user's balance at call
    start, so callers can cut the call short instead of letting it run up an
    uncollectible debt. Must be called inside db().
    """
    return estimate_coins_used_so_far(
        call_start_time, agent_llm_price_per_minute, llm_cost_multiplier, llm_rate_basis,
    ) >= user_balance


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
    print(f'metadata: {metadata}')#do remove
    raw_cost = float(metadata.get("cost") or 0)
    calculated_cost = calculate_conversation_cost(raw_cost)

    record.message_count = metadata.get("message_count")
    record.user_message_count = metadata.get("user_message_count")
    record.agent_message_count = metadata.get("agent_message_count")
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
    record.settings_version_id = get_or_create_current_settings_version(settings)
    agent = db.session.query(AgentModel).filter(AgentModel.id == record.agent_id).first()
    agent_llm_price = agent.llm_price_per_minute if agent else None
    llm_cost_multiplier = resolve_llm_cost_multiplier(agent, settings)

    # ---- LLM cost calibration snapshot: freeze what the agent's cost drivers
    # WERE at call time (no agent-versioning table exists yet to pull this
    # from historically). ----
    record.system_prompt_length = len(agent.system_prompt) if agent and agent.system_prompt else None
    record.tool_count = len(agent.agent_functions) if agent else None
    record.kb_total_pages = agent.kb_total_pages if agent else None
    record.rag_enabled = agent.rag_enabled if agent else None

    calculated = estimate_costs_credits(agent_llm_price, record.duration, settings, llm_cost_multiplier=llm_cost_multiplier)
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

    # Alert the user if they can no longer afford even the minimum call.
    _maybe_alert_insufficient_call_balance(record, settings)

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
