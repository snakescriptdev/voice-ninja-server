"""
WebSocket router — pure functional approach.

Structure:
  auth/          → authenticate_websocket_user()
  agent/         → fetch_and_validate_agent()
  limits/        → check_user_limits()
  bridge/        → browser_to_elevenlabs(), elevenlabs_to_browser()
  storage/       → save_conversation(), maybe_send_low_coins_alert()
  handler        → websocket_test_agent()  ← only orchestrates, zero logic
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse
from fastapi_sqlalchemy import db
from jose import JWTError, jwt
from sqlalchemy.orm import selectinload

from app_v2.core.config import VoiceSettings
from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY
from app_v2.core.logger import setup_logger
from app_v2.databases.models import (
    AgentModel,
    CoinUsageSettingsModel,
    ConversationsModel,
    UnifiedAuthModel,
)
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.utils.activity_logger import log_activity
from app_v2.utils.coin_utils import get_user_coin_balance, coins_to_inr
from app_v2.utils.conversation_lifecycle import (
    start_conversation,
    finalize_conversation,
    mark_conversation_failed,
    get_minimum_call_balance,
    is_balance_exhausted,
    is_agents_first_call,
    is_first_call_duration_exceeded,
    resolve_llm_cost_multiplier,
    resolve_llm_rate_basis,
    LOW_BALANCE_ERROR_MESSAGE,
    FIRST_CALL_DURATION_LIMIT_ERROR_MESSAGE,
)
from app_v2.utils.email_service import send_low_coins_email
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
from app_v2.utils.feature_access import (
    check_feature_limit_and_usage,
    get_feature_limit,
    get_feature_usage,
)
from app_v2.utils.jwt_utils import ALGORITHM, SECRET_KEY
from elevenlabs import ElevenLabs
logger = setup_logger(__name__)

router = APIRouter(prefix="/api/v2/agent", tags=["websocket"])


# ─────────────────────────────────────────────────────────────────────────────
# Data containers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AuthResult:
    user_id: int
    user: UnifiedAuthModel


@dataclass
class AgentResult:
    agent: AgentModel
    elevenlabs_agent_id: str


@dataclass
class LimitsResult:
    user_balance: int
    initial_usage: float
    minute_limit: Optional[float]


@dataclass
class CallContext:
    user_id: int
    agent: AgentModel
    elevenlabs_agent_id: str
    minute_limit: Optional[float]
    initial_usage: float
    call_start_time: datetime
    user_balance: int
    limit_reached: bool = False
    low_balance_reached: bool = False
    first_call_limit_reached: bool = False
    is_first_call: bool = False
    llm_cost_multiplier: float = 1.0
    llm_rate_basis: Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _receive_auth_message(websocket: WebSocket) -> Optional[dict]:
    """
    Waits up to 5 s for the first JSON message.
    Returns the parsed dict, or None on timeout.
    """
    try:
        return await asyncio.wait_for(websocket.receive_json(), timeout=5)
    except asyncio.TimeoutError:
        return None


def _decode_jwt(token: str) -> Optional[int]:
    """
    Decodes JWT and returns user_id, or None if invalid.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        return int(user_id) if user_id else None
    except JWTError:
        return None


def _load_and_validate_user(user_id: int) -> Optional[UnifiedAuthModel]:
    """
    Fetches user by ID. Returns None if not found or suspended.
    """
    try:
        user = UnifiedAuthModel.get_by_id(user_id)
        return None if user.is_suspended else user
    except Exception:
        return None


async def authenticate_websocket_user(websocket: WebSocket) -> Optional[AuthResult]:
    """
    Full auth pipeline:
      1. Receive first message within timeout
      2. Validate message shape
      3. Decode JWT → user_id
      4. Load + validate user

    Sends an error JSON and closes the socket on any failure.
    Returns AuthResult on success, None on failure.
    """
    async def _reject(message: str, reason: str) -> None:
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=reason)

    auth_msg = await _receive_auth_message(websocket)
    if auth_msg is None:
        await _reject("Auth timeout. Call disconnected.", "Auth timeout")
        logger.error("WebSocket auth timeout")
        return None

    if auth_msg.get("type") != "auth" or "token" not in auth_msg:
        await _reject("Auth required. Call disconnected.", "Auth required")
        return None

    user_id = _decode_jwt(auth_msg["token"])
    if user_id is None:
        await _reject("Invalid token. Call disconnected.", "Invalid token")
        logger.error("Invalid JWT received")
        return None

    user = _load_and_validate_user(user_id)
    if user is None:
        await _reject("User not found or suspended. Call disconnected.", "User invalid")
        logger.error(f"User {user_id} not found or suspended")
        return None

    return AuthResult(user_id=user_id, user=user)


# ─────────────────────────────────────────────────────────────────────────────
# Agent helpers
# ─────────────────────────────────────────────────────────────────────────────

def _query_agent(user_id: int, agent_id: int) -> Optional[AgentModel]:
    """
    Fetches agent owned by user_id. Eagerly loads agent_knowledge_bases and
    agent_functions since this agent instance outlives the db() session that
    loaded it (resolve_llm_cost_multiplier/resolve_llm_rate_basis touch these
    relationships from a later, separate db() block) — lazy loading them then
    would raise DetachedInstanceError.
    """
    return (
        db.session.query(AgentModel)
        .options(
            selectinload(AgentModel.agent_knowledge_bases),
            selectinload(AgentModel.agent_functions),
        )
        .filter(AgentModel.id == agent_id, AgentModel.user_id == user_id)
        .first()
    )


async def fetch_and_validate_agent(
    websocket: WebSocket,
    user_id: int,
    agent_id: int,
) -> Optional[AgentResult]:
    """
    Fetches agent, checks existence and enabled state.
    Rejects websocket and returns None on any failure.
    """
    async def _reject(message: str, reason: str) -> None:
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=reason)

    with db():
        agent = _query_agent(user_id, agent_id)

    if not agent:
        await _reject("Agent not found. Call disconnected.", "Agent not found")
        logger.error(f"Agent {agent_id} not found for user {user_id}")
        return None

    if not agent.is_enabled:
        await _reject("Agent is disabled. Call disconnected.", "Agent is disabled")
        logger.error(f"Agent {agent_id} is disabled")
        return None

    if not agent.elevenlabs_agent_id:
        await _reject("Agent misconfigured. Call disconnected.", "Missing EL agent ID")
        logger.error(f"Agent {agent_id} missing elevenlabs_agent_id")
        return None

    return AgentResult(agent=agent, elevenlabs_agent_id=agent.elevenlabs_agent_id)


# ─────────────────────────────────────────────────────────────────────────────
# Limits helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_monthly_limit_ok(user_id: int) -> bool:
    """Returns True if user is within monthly minute limit."""
    try:
        check_feature_limit_and_usage(user_id, "monthly_minutes")
        return True
    except Exception:
        return False


def _has_sufficient_coins(user_balance: int) -> tuple[bool, int]:
    """
    Returns (is_sufficient, minimum_required).
    Keeps the threshold calculation in one place so it can be logged clearly.
    """
    minimum = get_minimum_call_balance()
    return user_balance >= minimum, minimum


async def check_user_limits(
    websocket: WebSocket,
    user_id: int,
    agent_id: int,
) -> Optional[LimitsResult]:
    """
    Checks coin balance (minimum 3-minute threshold) and monthly minutes limit.
    All DB calls are wrapped in a single db() context to avoid session errors.
    Rejects websocket and returns None on any failure.

    A rejection due to the monthly minutes limit specifically also creates a
    failed conversation row (instead of no row at all) so the attempt is
    visible in the conversation history with the reason recorded.
    """
    async def _reject(message: str, reason: str) -> None:
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=reason)

    with db():
        user_balance = get_user_coin_balance(user_id)
        sufficient, minimum_required = _has_sufficient_coins(user_balance)

        if not sufficient:
            await _reject(
                f"Insufficient coins. Minimum {minimum_required} coins required to start a call.",
                "Insufficient coins",
            )
            logger.error(
                f"User {user_id} has insufficient coins "
                f"(balance={user_balance}, required={minimum_required})"
            )
            return None

        if not _is_monthly_limit_ok(user_id):
            conversation_row_id = start_conversation(user_id, agent_id, ChannelEnum.test_voice)
            mark_conversation_failed(conversation_row_id, "Monthly minutes limit reached")
            await _reject(
                "Monthly minutes limit reached. Call disconnected.",
                "Monthly minutes limit reached",
            )
            logger.error(f"User {user_id} hit monthly minutes limit")
            return None

        initial_usage = get_feature_usage(user_id, "monthly_minutes")
        minute_limit = get_feature_limit(user_id, "monthly_minutes")

    return LimitsResult(
        user_balance=user_balance,
        initial_usage=initial_usage,
        minute_limit=minute_limit,
    )

async def check_elevenlabs_credits(
    websocket: WebSocket,
    user_id: int,
    agent_id: int,
    channel: ChannelEnum = ChannelEnum.test_voice,
) -> bool:
    """
    Checks the ElevenLabs subscription's remaining character credits.

    On low credits or an API error, persists a failed conversation record
    (for admin visibility), notifies the browser, and closes the socket.
    """
    async def _reject() -> None:
        error_message = "Some error occurred on server, please contact administrator."
        with db():
            record = ConversationsModel(
                agent_id=agent_id,
                user_id=user_id,
                call_status=CallStatusEnum.failed,
                channel=channel,
                error_message = error_message,
                transcript_summary=error_message,
            )
            db.session.add(record)
            db.session.commit()

        await websocket.send_json({"type": "error", "message": error_message})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="ElevenLabs credits exhausted")

    try:
        client = ElevenLabs(api_key=VoiceSettings.ELEVENLABS_API_KEY)
        subscription = client.user.subscription.get()
        character_count = getattr(subscription, "character_count", 0)
        character_limit = getattr(subscription, "character_limit", 0)
        credits_left = character_limit - character_count
        if credits_left <= 10:
            logger.info(f"Credits left: {credits_left}")
            await _reject()
            return False

    except Exception as ex:
        logger.exception(f"check_elevenlabs_credits failed: {str(ex)}")
        await _reject()
        return False

    return True

# ─────────────────────────────────────────────────────────────────────────────
# Activity logging helpers
# ─────────────────────────────────────────────────────────────────────────────

def log_conversation_started(user_id: int, agent_id: int, agent: AgentModel, elevenlabs_agent_id: str) -> None:
    with db():
        log_activity(
            user_id=user_id,
            event_type="agent_conversation_started",
            description=f"Started voice chat for agent: {agent.agent_name}",
            metadata={
                "agent_id": agent_id,
                "agent_name": agent.agent_name,
                "elevenlabs_agent_id": elevenlabs_agent_id,
            },
        )


def log_conversation_completed(
    user_id: int,
    agent_id: int,
    agent: AgentModel,
    elevenlabs_agent_id: str,
    conversation_id: Optional[str],
) -> None:
    with db():
        log_activity(
            user_id=user_id,
            event_type="agent_conversation_completed",
            description=f"Completed voice chat for agent: {agent.agent_name}",
            metadata={
                "agent_id": agent_id,
                "agent_name": agent.agent_name,
                "elevenlabs_agent_id": elevenlabs_agent_id,
                "conversation_id": conversation_id,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Bridge tasks
# ─────────────────────────────────────────────────────────────────────────────

async def browser_to_elevenlabs(
    websocket: WebSocket,
    el_ws: aiohttp.ClientWebSocketResponse,
    ctx: CallContext,
) -> None:
    """
    Relays audio/text from browser → ElevenLabs.
    Auto-disconnects when monthly minute limit is reached.
    """
    chunk_count = 0
    try:
        while True:
            if chunk_count % 10 == 0:
                elapsed_min = (datetime.now(timezone.utc) - ctx.call_start_time).total_seconds() / 60
                if ctx.minute_limit is not None and (ctx.initial_usage + elapsed_min) >= ctx.minute_limit:
                    logger.warning(f"Auto-disconnect user {ctx.user_id}: monthly minutes limit")
                    ctx.limit_reached = True
                    await websocket.send_json({"type": "error", "message": "Monthly minutes limit reached."})
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

                with db():
                    low_balance = is_balance_exhausted(
                        ctx.call_start_time,
                        ctx.user_balance,
                        agent_llm_price_per_minute=ctx.agent.llm_price_per_minute,
                        llm_cost_multiplier=ctx.llm_cost_multiplier,
                        llm_rate_basis=ctx.llm_rate_basis,
                    )
                if low_balance:
                    logger.warning(f"Auto-disconnect user {ctx.user_id}: low balance")
                    ctx.low_balance_reached = True
                    await websocket.send_json({"type": "error", "message": "Low balance. Call ended to avoid exceeding your available credits."})
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

                with db():
                    first_call_capped = is_first_call_duration_exceeded(ctx.call_start_time, ctx.is_first_call)
                if first_call_capped:
                    logger.warning(f"Auto-disconnect user {ctx.user_id}: first-call duration limit")
                    ctx.first_call_limit_reached = True
                    await websocket.send_json({"type": "error", "message": "First call duration limit reached. Call ended."})
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

            message = await websocket.receive()
            if "bytes" in message:
                if el_ws.closed:
                    logger.info("ElevenLabs socket already closed; stopping browser relay")
                    break
                chunk_count += 1
                await el_ws.send_json({"user_audio_chunk": base64.b64encode(message["bytes"]).decode()})
            elif "text" in message:
                await el_ws.send_json(json.loads(message["text"]))
            elif message["type"] == "websocket.disconnect":
                logger.info("Browser sent disconnect")
                break

    except WebSocketDisconnect:
        logger.info("Browser disconnected (WebSocketDisconnect)")
    except ConnectionResetError:
        # ElevenLabs closed its side (e.g. silence timeout) between our
        # el_ws.closed check and the send — expected race, not an error.
        logger.info("ElevenLabs connection reset while relaying audio (likely EL-side timeout)")
    except Exception as e:
        logger.error(f"{str(e)} : browser_to_elevenlabs error:\n{traceback.format_exc()}")
    finally:
        if not el_ws.closed:
            await el_ws.close()


async def elevenlabs_to_browser(
    websocket: WebSocket,
    el_ws: aiohttp.ClientWebSocketResponse,
) -> Optional[str]:
    """
    Relays events/audio from ElevenLabs → browser.
    Returns the conversation_id when available.

    Audio chunks are already in flight over the socket at the moment the user
    barges in, so the `interruption` event alone doesn't stop them from arriving.
    We track the last interruption's event_id and drop any audio event at or
    below it (matching the official SDK's own Conversation class), otherwise
    that stale agent audio gets relayed and played right as the user is
    speaking — bleeding into the mic and corrupting what ElevenLabs transcribes.
    """
    conversation_id: Optional[str] = None
    last_interrupt_id = 0
    try:
        async for msg in el_ws:

            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                etype = data.get("type")

                if etype == "conversation_initiation_metadata":
                    conversation_id = (
                        data.get("conversation_initiation_metadata_event", {})
                        .get("conversation_id")
                    )
                    logger.info(f"Conversation ID captured: {conversation_id}")

                if etype == "interruption":
                    last_interrupt_id = int(data.get("interruption_event", {}).get("event_id", 0))

                if etype == "audio":
                    audio_event = data.get("audio_event", {})
                    if int(audio_event.get("event_id", 0)) <= last_interrupt_id:
                        continue
                    audio_b64 = audio_event.get("audio_base_64")
                    if audio_b64:
                        await websocket.send_bytes(base64.b64decode(audio_b64))
                        data["audio_event"]["audio_base_64"] = "[STRIPPED]"
                        await websocket.send_json(data)
                else:
                    await websocket.send_json(data)
                    if etype and etype != "ping":
                        logger.info(f"Relayed EL event: {etype}")

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                logger.info(f"ElevenLabs WS closed/errored: {msg.type}")
                break

        # Reached only when the EL socket ended on its own (silence timeout,
        # agent hangup, etc.) — not when this task is cancelled because the
        # browser disconnected first. Tell the browser explicitly instead of
        # just dropping the connection.
        try:
            await websocket.send_json({
                "type": "call_ended",
                "message": "The agent ended the call.",
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

    except asyncio.CancelledError:
        pass
    except Exception:
        logger.error(f"elevenlabs_to_browser error:\n{traceback.format_exc()}")
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass

    return conversation_id


async def run_bridge(
    websocket: WebSocket,
    el_ws: aiohttp.ClientWebSocketResponse,
    ctx: CallContext,
) -> Optional[str]:
    """
    Runs both bridge tasks concurrently.
    Cancels the slower one when the first completes.
    Returns conversation_id.
    """
    conversation_id_holder: list[Optional[str]] = [None]

    async def _el_to_browser_wrapper():
        conversation_id_holder[0] = await elevenlabs_to_browser(websocket, el_ws)

    tasks = [
        asyncio.create_task(browser_to_elevenlabs(websocket, el_ws, ctx), name="browser_task"),
        asyncio.create_task(_el_to_browser_wrapper(), name="elevenlabs_task"),
    ]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        logger.info(f"Cancelling task: {task.get_name()}")
        task.cancel()

    if pending:
        # elevenlabs_to_browser() swallows CancelledError and returns the
        # conversation_id it already captured — but only once it actually
        # resumes and runs its finally/return path. Without this wait,
        # cancel() merely schedules that resumption and we'd read the
        # holder before it's populated, losing the conversation record.
        await asyncio.wait(pending)

    return conversation_id_holder[0]


# ─────────────────────────────────────────────────────────────────────────────
# Post-call storage helpers
# ─────────────────────────────────────────────────────────────────────────────


async def maybe_send_low_coins_alert(user_id: int) -> None:
    """Sends low-coins email if user has alerts enabled and balance ≤ 1000."""
    try:
        with db():
            user = db.session.query(UnifiedAuthModel).get(user_id)
            if not user:
                return
            alerts_enabled = (
                user.notification_settings and user.notification_settings.useage_alerts
            )
            if not alerts_enabled or not user.email:
                return

            current_balance = get_user_coin_balance(user_id)
            if current_balance > 1000:
                return

            credits_per_rupee = CoinUsageSettingsModel.get_settings().credits_per_rupee
            await send_low_coins_email(
                user_email=user.email,
                current_balance_inr=coins_to_inr(current_balance, credits_per_rupee),
                base_url=VoiceSettings.FRONTEND_URL,
                user_name=user.first_name or user.name or "User",
            )
            logger.info(f"Low coins email sent to {user.email} (balance={current_balance})")
    except Exception:
        logger.error(f"Low coins alert failed:\n{traceback.format_exc()}")


async def save_conversation(
    user_id: int,
    agent_id: int,
    conversation_id: str,
    conversation_row_id: int,
    error_message: Optional[str] = None,
) -> None:
    """
    Fetches ElevenLabs metadata, finalizes the in_progress conversation row
    created at call start, deducts coins, and triggers low-balance alert if
    needed.

    error_message: passed through to finalize_conversation() when the call
    still produced real metadata but was cut short for a known reason (e.g.
    monthly minutes limit) — preserves transcript/history instead of
    discarding it via mark_conversation_failed().
    """
    try:
        el_conv = ElevenLabsConversation()
        metadata = await asyncio.to_thread(el_conv.extract_conversation_metadata, conversation_id)

        if not metadata:
            logger.error(f"Metadata extraction failed for conversation {conversation_id}")
            with db():
                mark_conversation_failed(conversation_row_id, error_message or "Metadata extraction failed")
            return

        with db():
            record = finalize_conversation(conversation_row_id, metadata, conversation_id, error_message=error_message)

        logger.info(
            f"Conversation {conversation_id} saved "
            f"(duration={metadata.get('duration')}s, "
            f"messages={metadata.get('message_count')}, "
            f"cost={record.cost})"
        )

        await maybe_send_low_coins_alert(user_id)

    except Exception:
        logger.error(f"save_conversation failed:\n{traceback.format_exc()}")
        with db():
            mark_conversation_failed(conversation_row_id, "Failed to save conversation")


# ─────────────────────────────────────────────────────────────────────────────
# Route handlers
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/test-page", response_class=HTMLResponse)
async def get_test_page():
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "agent_test.html")
    with open(template_path) as f:
        return f.read()


@router.websocket("/{agent_id}/test-connection")
async def websocket_test_agent(websocket: WebSocket, agent_id: int):
    """
    Pure orchestration — zero business logic lives here.

    Flow:
      1. Accept connection
      2. Authenticate user (JWT via first message)
      3. Validate agent ownership + enabled state
      4. Check coin balance + monthly minute limit
      5. Log call start
      6. Open ElevenLabs WS + run audio bridge
      7. Log call end
      8. Save conversation + deduct coins + low-balance alert
    """
    await websocket.accept()

    # ── 1. Auth ───────────────────────────────────────────────────────────────
    auth = await authenticate_websocket_user(websocket)
    if not auth:
        return

    # ── 2. Agent validation ───────────────────────────────────────────────────
    agent_result = await fetch_and_validate_agent(websocket, auth.user_id, agent_id)
    if not agent_result:
        return

    # ── 3. Limits check ───────────────────────────────────────────────────────
    limits = await check_user_limits(websocket, auth.user_id, agent_id)
    if not limits:
        return
    
    has_credits = await check_elevenlabs_credits(websocket, auth.user_id, agent_id)
    if not has_credits:
        return

    # ── 4. Build call context ─────────────────────────────────────────────────
    with db():
        settings = CoinUsageSettingsModel.get_settings()
        is_first_call = is_agents_first_call(agent_id)
        llm_cost_multiplier = resolve_llm_cost_multiplier(agent_result.agent, settings)
        llm_rate_basis = None if is_first_call else resolve_llm_rate_basis(agent_result.agent)

    ctx = CallContext(
        user_id=auth.user_id,
        agent=agent_result.agent,
        elevenlabs_agent_id=agent_result.elevenlabs_agent_id,
        minute_limit=limits.minute_limit,
        initial_usage=limits.initial_usage,
        call_start_time=datetime.now(timezone.utc),
        user_balance=limits.user_balance,
        is_first_call=is_first_call,
        llm_cost_multiplier=llm_cost_multiplier,
        llm_rate_basis=llm_rate_basis,
    )

    # ── 5. Log start ──────────────────────────────────────────────────────────
    log_conversation_started(auth.user_id, agent_id, agent_result.agent, agent_result.elevenlabs_agent_id)
    logger.info(f"Bridge starting for agent {agent_id} (EL: {agent_result.elevenlabs_agent_id})")

    with db():
        conversation_row_id = start_conversation(auth.user_id, agent_id, ChannelEnum.test_voice)

    # ── 6. ElevenLabs bridge ──────────────────────────────────────────────────
    if not ELEVENLABS_API_KEY:
        logger.error("ELEVENLABS_API_KEY not set")
        with db():
            mark_conversation_failed(conversation_row_id, "Server misconfiguration: ELEVENLABS_API_KEY missing")
        await websocket.send_json({"type": "error", "message": "Server misconfiguration. Call disconnected."})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="ELEVENLABS_API_KEY missing")
        return

    el_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_result.elevenlabs_agent_id}"
    conversation_id: Optional[str] = None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(el_url, headers={"xi-api-key": ELEVENLABS_API_KEY}) as el_ws:
                logger.info(f"ElevenLabs WS connected for agent {agent_result.elevenlabs_agent_id}")
                conversation_id = await run_bridge(websocket, el_ws, ctx)
    except Exception:
        logger.error(f"ElevenLabs bridge failed:\n{traceback.format_exc()}")
        with db():
            mark_conversation_failed(conversation_row_id, "Failed to connect to ElevenLabs")
        return

    if ctx.limit_reached:
        limit_error = "Monthly minutes limit reached"
    elif ctx.low_balance_reached:
        limit_error = LOW_BALANCE_ERROR_MESSAGE
    elif ctx.first_call_limit_reached:
        limit_error = FIRST_CALL_DURATION_LIMIT_ERROR_MESSAGE
    else:
        limit_error = None
    if limit_error:
        logger.warning(f"Call for user {auth.user_id} ended: {limit_error}")

    # ── 7. Log completion ─────────────────────────────────────────────────────
    log_conversation_completed(auth.user_id, agent_id, agent_result.agent, agent_result.elevenlabs_agent_id, conversation_id)

    # ── 8. Persist & alert ─────────────────────────────────────────────────────
    # Even when the monthly limit ended the call, if a conversation_id was
    # captured real EL metadata exists — finalize normally (with the limit
    # note attached) so transcript/history still shows up, instead of
    # discarding it via mark_conversation_failed().
    if not conversation_id:
        logger.warning("No conversation_id captured — skipping save.")
        with db():
            mark_conversation_failed(conversation_row_id, limit_error or "No conversation ID captured")
        return

    await save_conversation(auth.user_id, agent_id, conversation_id, conversation_row_id, error_message=limit_error)


@router.get("/{agent_id}/test-connection/info", tags=["WebSocket"])
def websocket_test_agent_info(agent_id: int):
    return {
        "endpoint": f"/{agent_id}/test-connection",
        "method": "WEBSOCKET",
        "url_format": f"ws://<host>/api/v2/agent/{agent_id}/test-connection",
        "authentication": {
            "type": "JWT",
            "mode": "first_message",
            "message_format": {"type": "auth", "token": "<JWT>"},
            "note": "Send auth message immediately after connection opens.",
        },
        "client_flow": [
            "1. Open WebSocket connection",
            "2. Send auth message as first JSON payload",
            "3. Start sending PCM 16k audio bytes",
        ],
        "timeouts": {"auth_timeout_seconds": 5},
        "close_codes": {
            "1008": "Policy violation (auth / agent / limits check failed)",
            "1011": "Internal server error",
        },
    }