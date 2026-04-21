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
from app_v2.utils.coin_utils import deduct_coins, get_user_coin_balance
from app_v2.utils.email_service import send_low_coins_email
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
from app_v2.utils.feature_access import (
    check_feature_limit_and_usage,
    get_feature_limit,
    get_feature_usage,
)
from app_v2.utils.jwt_utils import ALGORITHM, SECRET_KEY

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
    """Fetches agent owned by user_id."""
    return (
        db.session.query(AgentModel)
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


def _get_minimum_call_balance() -> int:
    """
    Calculates the minimum coin balance required to start a call.

    Formula:
        minimum = (3 × cost_per_minute_in_coins) + static_conversation_cost

    Rationale: user must afford at least 3 minutes + the flat per-call fee
    before we even open the ElevenLabs socket.
    """
    settings = CoinUsageSettingsModel.get_settings()
    return int((3 * settings.cost_per_minute_in_coins) + settings.static_conversation_cost)


def _has_sufficient_coins(user_balance: int) -> tuple[bool, int]:
    """
    Returns (is_sufficient, minimum_required).
    Keeps the threshold calculation in one place so it can be logged clearly.
    """
    minimum = _get_minimum_call_balance()
    return user_balance >= minimum, minimum


async def check_user_limits(
    websocket: WebSocket,
    user_id: int,
) -> Optional[LimitsResult]:
    """
    Checks coin balance (minimum 3-minute threshold) and monthly minutes limit.
    All DB calls are wrapped in a single db() context to avoid session errors.
    Rejects websocket and returns None on any failure.
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
                    await websocket.send_json({"type": "error", "message": "Monthly minutes limit reached."})
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

            message = await websocket.receive()
            if "bytes" in message:
                chunk_count += 1
                await el_ws.send_json({"user_audio_chunk": base64.b64encode(message["bytes"]).decode()})
            elif "text" in message:
                await el_ws.send_json(json.loads(message["text"]))
            elif message["type"] == "websocket.disconnect":
                logger.info("Browser sent disconnect")
                break

    except WebSocketDisconnect:
        logger.info("Browser disconnected (WebSocketDisconnect)")
    except Exception:
        logger.error(f"browser_to_elevenlabs error:\n{traceback.format_exc()}")
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
    """
    conversation_id: Optional[str] = None
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

                if etype == "audio":
                    audio_b64 = data.get("audio_event", {}).get("audio_base_64")
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

    return conversation_id_holder[0]


# ─────────────────────────────────────────────────────────────────────────────
# Post-call storage helpers
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_cost(raw_el_cost: float) -> int:
    settings = CoinUsageSettingsModel.get_settings()
    return int((raw_el_cost * settings.elevenlabs_multiplier) + settings.static_conversation_cost)


def _persist_conversation(
    user_id: int,
    agent_id: int,
    metadata: dict,
    conversation_id: str,
) -> ConversationsModel:
    """
    Saves conversation record and deducts coins. Must be called inside db().

    force=True is passed to deduct_coins so that if the call cost exceeded
    the user's balance (overdraft), the full cost is still recorded and the
    balance goes negative rather than silently skipping the deduction.
    """
    raw_cost = float(metadata.get("cost") or 0)
    calculated_cost = _calculate_cost(raw_cost)
    call_status = CallStatusEnum.success if metadata.get("call_successful") else CallStatusEnum.failed

    record = ConversationsModel(
        agent_id=agent_id,
        user_id=user_id,
        message_count=metadata.get("message_count"),
        duration=metadata.get("duration"),
        call_status=call_status,
        channel=ChannelEnum.chat,
        transcript_summary=metadata.get("transcript_summary"),
        elevenlabs_conv_id=conversation_id,
        cost=raw_cost,
    )
    db.session.add(record)
    db.session.flush()

    if calculated_cost > 0:
        deduct_coins(
            user_id=user_id,
            amount=calculated_cost,
            reference_type="conversation",
            reference_id=record.id,
            commit=False,
            force=True,  # call already happened — always deduct full cost
        )

    db.session.commit()
    db.session.refresh(record)
    return record


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

            await send_low_coins_email(
                user_email=user.email,
                current_coins=current_balance,
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
) -> None:
    """
    Fetches ElevenLabs metadata, persists the conversation record,
    deducts coins, and triggers low-balance alert if needed.
    """
    try:
        el_conv = ElevenLabsConversation()
        metadata = await asyncio.to_thread(el_conv.extract_conversation_metadata, conversation_id)

        if not metadata:
            logger.error(f"Metadata extraction failed for conversation {conversation_id}")
            return

        with db():
            record = _persist_conversation(user_id, agent_id, metadata, conversation_id)

        logger.info(
            f"Conversation {conversation_id} saved "
            f"(duration={metadata.get('duration')}s, "
            f"messages={metadata.get('message_count')}, "
            f"cost={record.cost})"
        )

        await maybe_send_low_coins_alert(user_id)

    except Exception:
        logger.error(f"save_conversation failed:\n{traceback.format_exc()}")


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
    limits = await check_user_limits(websocket, auth.user_id)
    if not limits:
        return

    # ── 4. Build call context ─────────────────────────────────────────────────
    ctx = CallContext(
        user_id=auth.user_id,
        agent=agent_result.agent,
        elevenlabs_agent_id=agent_result.elevenlabs_agent_id,
        minute_limit=limits.minute_limit,
        initial_usage=limits.initial_usage,
        call_start_time=datetime.now(timezone.utc),
    )

    # ── 5. Log start ──────────────────────────────────────────────────────────
    log_conversation_started(auth.user_id, agent_id, agent_result.agent, agent_result.elevenlabs_agent_id)
    logger.info(f"Bridge starting for agent {agent_id} (EL: {agent_result.elevenlabs_agent_id})")

    # ── 6. ElevenLabs bridge ──────────────────────────────────────────────────
    if not ELEVENLABS_API_KEY:
        logger.error("ELEVENLABS_API_KEY not set")
        await websocket.send_json({"type": "error", "message": "Server misconfiguration. Call disconnected."})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="ELEVENLABS_API_KEY missing")
        return

    el_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_result.elevenlabs_agent_id}"
    conversation_id: Optional[str] = None

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(el_url, headers={"xi-api-key": ELEVENLABS_API_KEY}) as el_ws:
            logger.info(f"ElevenLabs WS connected for agent {agent_result.elevenlabs_agent_id}")
            conversation_id = await run_bridge(websocket, el_ws, ctx)

    # ── 7. Log completion ─────────────────────────────────────────────────────
    log_conversation_completed(auth.user_id, agent_id, agent_result.agent, agent_result.elevenlabs_agent_id, conversation_id)

    # ── 8. Persist & alert ────────────────────────────────────────────────────
    if not conversation_id:
        logger.warning("No conversation_id captured — skipping save.")
        return

    await save_conversation(auth.user_id, agent_id, conversation_id)


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