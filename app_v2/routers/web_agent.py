"""
Web Agent router
Structure:
  validation/    → fetch_and_validate_web_agent(), check_owner_limits()
  bridge/        → BrowserAudioInterface, run_web_agent_session()
  storage/       → save_web_conversation(), maybe_send_notifications()
  activity/      → log_web_chat_started(), log_web_chat_ended()
  routes/        → embed_script, ws proxy, config, lead — all thin orchestrators
"""

from __future__ import annotations

import asyncio
import base64
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi_sqlalchemy import db

from app_v2.core.config import VoiceSettings
from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY
from app_v2.core.logger import setup_logger
from app_v2.databases.models import (
    AgentModel,
    ConversationsModel,
    CoinUsageSettingsModel,
    UnifiedAuthModel,
    WebAgentLeadModel,
    WebAgentModel,
)
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
from app_v2.schemas.web_agent_schema import WebAgentLeadCreate, WebAgentPublicConfig
from app_v2.utils.activity_logger import log_activity
from app_v2.utils.coin_utils import deduct_coins, get_user_coin_balance
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
from app_v2.utils.email_service import send_conversation_notification_email, send_low_coins_email
from app_v2.utils.feature_access import (
    check_feature_limit_and_usage,
    get_feature_limit,
    get_feature_usage,
)
from app_v2.utils.jwt_utils import HTTPBearer, get_current_user

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(prefix="/api/v2/web-agent", tags=["web-agent"])

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

VOICE_NINJA_LOGO_SVG = """<svg width="62" height="21" viewBox="0 0 62 21" fill="none" xmlns="http://www.w3.org/2000/svg">
<path fill-rule="evenodd" clip-rule="evenodd" d="M0 20.7579C0 13.9598 5.51102 8.44873 12.3092 8.44873H48.9212C48.9212 15.2469 43.4102 20.7579 36.612 20.7579H0ZM20.3495 17.188C19.5605 18.7218 16.946 18.9494 14.5099 17.6963C12.0738 16.4432 10.2573 13.9279 11.0463 12.3941C11.8353 10.8602 16.0273 12.7191 18.4634 13.9722C18.556 14.0342 18.646 14.0941 18.7333 14.1523C20.4231 15.2785 21.0997 15.7294 20.3495 17.188ZM34.8439 17.6963C32.4078 18.9494 29.7933 18.7218 29.0043 17.188C28.2541 15.7294 28.9306 15.2785 30.6205 14.1523C30.7078 14.0941 30.7977 14.0342 30.8904 13.9722C33.3265 12.7191 37.5185 10.8602 38.3074 12.3941C39.0964 13.9279 37.28 16.4432 34.8439 17.6963Z" fill="url(#paint0_linear_113_516)"/>
<path d="M49.8682 6.5552C49.8682 3.41757 52.4117 0.874023 55.5493 0.874023H61.5461C61.5461 4.01165 59.0026 6.5552 55.865 6.5552H49.8682Z" fill="url(#paint1_linear_113_516)"/>
<defs>
<linearGradient id="paint0_linear_113_516" x1="0" y1="14.6033" x2="48.9212" y2="14.6033" gradientUnits="userSpaceOnUse"><stop stop-color="#E06943"/><stop offset="0.425" stop-color="#AC1E7A"/><stop offset="0.775" stop-color="#562C7C"/><stop offset="1" stop-color="#34399B"/></linearGradient>
<linearGradient id="paint1_linear_113_516" x1="49.8682" y1="3.71461" x2="61.5461" y2="3.71461" gradientUnits="userSpaceOnUse"><stop stop-color="#E06943"/><stop offset="0.425" stop-color="#AC1E7A"/><stop offset="0.775" stop-color="#562C7C"/><stop offset="1" stop-color="#34399B"/></linearGradient>
</defs>
</svg>"""


# ─────────────────────────────────────────────────────────────────────────────
# Data containers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WebAgentContext:
    """All data needed to run a web agent session, extracted before WS bridge starts."""
    user_id: int
    agent_id: int
    agent_name: str
    web_agent_name: str
    public_id: str
    elevenlabs_agent_id: str
    initial_usage: float
    minute_limit: Optional[float]
    call_start_time: datetime


@dataclass
class OwnerNotificationSettings:
    email: Optional[str]
    name: str
    email_notifications: bool
    usage_alerts: bool


# ─────────────────────────────────────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reject_ws(websocket: WebSocket, message: str, code: int = 1008):
    """Fire-and-forget WS rejection coroutine — caller must await."""
    async def _inner():
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close(code=code,reason=message)
    return _inner()


def _get_minimum_call_balance() -> int:
    """
    Minimum coins required to start a call (must be inside db() context).

    Formula: (3 × cost_per_minute_in_coins) + static_conversation_cost
    """
    settings = CoinUsageSettingsModel.get_settings()
    return int((3 * settings.cost_per_minute_in_coins) + settings.static_conversation_cost)


def _has_sufficient_coins(user_balance: int) -> tuple[bool, int]:
    """Returns (is_sufficient, minimum_required). Must be inside db() context."""
    minimum = _get_minimum_call_balance()
    return user_balance >= minimum, minimum


async def fetch_and_validate_web_agent(
    websocket: WebSocket,
    public_id: str,
) -> Optional[WebAgentContext]:
    """
    Loads WebAgentModel, validates it and its owner's limits.
    All DB calls in a single db() context.
    Returns WebAgentContext on success, None (after rejecting WS) on failure.
    """
    with db():
        web_agent: Optional[WebAgentModel] = (
            db.session.query(WebAgentModel)
            .filter(WebAgentModel.public_id == public_id)
            .first()
        )

        if not web_agent:
            await _reject_ws(websocket, "Web Agent not found")
            return None

        if not web_agent.is_enabled:
            await _reject_ws(websocket, "Web Agent is disabled")
            return None

        if not web_agent.agent or not web_agent.agent.elevenlabs_agent_id:
            await _reject_ws(websocket, "Agent not configured")
            return None

        user_id: int = web_agent.user_id
        owner_balance = get_user_coin_balance(user_id)
        sufficient, minimum_required = _has_sufficient_coins(owner_balance)

        if not sufficient:
            logger.error(
                "Owner %s has insufficient coins (balance=%s, required=%s)",
                user_id, owner_balance, minimum_required,
            )
            await _reject_ws(
                websocket,
                f"Insufficient coins. Minimum {minimum_required} coins required.",
            )
            return None

        try:
            check_feature_limit_and_usage(user_id, "monthly_minutes")
        except HTTPException as e:
            await _reject_ws(websocket, e.detail)
            logger.error("Monthly minutes limit for owner %s: %s", user_id, e.detail)
            return None

        return WebAgentContext(
            user_id=user_id,
            agent_id=web_agent.agent_id,
            agent_name=web_agent.agent.agent_name,
            web_agent_name=web_agent.web_agent_name,
            public_id=public_id,
            elevenlabs_agent_id=web_agent.agent.elevenlabs_agent_id,
            initial_usage=get_feature_usage(user_id, "monthly_minutes"),
            minute_limit=get_feature_limit(user_id, "monthly_minutes"),
            call_start_time=datetime.now(timezone.utc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# BrowserAudioInterface
# ─────────────────────────────────────────────────────────────────────────────

class BrowserAudioInterface:
    """
    Bridges ElevenLabs Conversation audio with the browser WebSocket.
      output()          → sends PCM chunks to browser as base64 JSON
      push_user_audio() → forwards browser audio into ElevenLabs input callback
    """

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop, call_id: str):
        self.websocket = websocket
        self.loop = loop
        self.call_id = call_id
        self._input_cb = None

    def _send(self, payload: dict) -> None:
        """Thread-safe fire-and-forget send to the browser."""
        try:
            if self.websocket.client_state.name == "CONNECTED":
                asyncio.run_coroutine_threadsafe(
                    self.websocket.send_json(payload), self.loop
                )
        except Exception as e:
            logger.error("BrowserAudioInterface send error: %s", e)

    def start(self, input_callback) -> None:
        self._input_cb = input_callback
        logger.info("BrowserAudioInterface started for call_id=%s", self.call_id)
        self._send({
            "type": "audio_interface_ready",
            "message": "Audio interface is now active",
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    def stop(self) -> None:
        self._input_cb = None
        logger.info("BrowserAudioInterface stopped for call_id=%s", self.call_id)

    def output(self, audio: bytes) -> None:
        self._send({
            "type": "audio_chunk",
            "sample_rate": 16000,
            "channels": 1,
            "format": "pcm_s16le",
            "data_b64": base64.b64encode(audio).decode("ascii"),
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    def interrupt(self) -> None:
        pass

    def push_user_audio(self, audio: bytes) -> None:
        if self._input_cb and audio:
            try:
                self._input_cb(audio)
            except Exception as e:
                logger.error("Error delivering audio to ElevenLabs: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# ElevenLabs session helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_model(language: str, model: str) -> str:
    """
    Ensures model is compatible with the selected language.
    English-only models are rejected for non-English languages.
    """
    en_codes = {"en", "en-US", "en-GB"}
    en_models = {"eleven_turbo_v2", "eleven_flash_v2"}
    multi_models = {"eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_multilingual_v2"}

    if language in en_codes:
        return model if model in en_models else "eleven_turbo_v2"
    return model if model in multi_models else "eleven_turbo_v2_5"


def _make_on_agent_response(websocket: WebSocket, loop: asyncio.AbstractEventLoop):
    def on_agent_response(text: str) -> None:
        try:
            if websocket.client_state.name == "CONNECTED":
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({
                        "type": "agent_response",
                        "text": text,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }),
                    loop,
                )
        except Exception as e:
            logger.error("on_agent_response error: %s", e)
    return on_agent_response


def _make_on_user_transcript(websocket: WebSocket, loop: asyncio.AbstractEventLoop):
    def on_user_transcript(text: str) -> None:
        try:
            if websocket.client_state.name == "CONNECTED":
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({
                        "type": "user_transcript",
                        "text": text,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }),
                    loop,
                )
        except Exception as e:
            logger.error("on_user_transcript error: %s", e)
    return on_user_transcript


async def _start_elevenlabs_conversation(
    websocket: WebSocket,
    audio_if: BrowserAudioInterface,
    ctx: WebAgentContext,
    language: str,
    model: str,
) -> Optional[object]:
    """
    Initialises and starts an ElevenLabs Conversation session.
    Returns the Conversation object on success, None on failure.
    """
    try:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import Conversation, ConversationInitiationData
    except ImportError:
        await websocket.send_json({"type": "error", "message": "ElevenLabs SDK not available"})
        await websocket.close(code=1011)
        return None

    api_key = ELEVENLABS_API_KEY or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        await websocket.send_json({"type": "error", "message": "Server configuration error"})
        await websocket.close(code=1011)
        return None

    loop = asyncio.get_running_loop()
    call_id = f"web_{ctx.agent_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    try:
        el_client = ElevenLabs(api_key=api_key)
        config = ConversationInitiationData(
            user_id=f"web_{ctx.agent_id}",
            extra_body={"model": _resolve_model(language, model)},
            dynamic_variables={"call_id": call_id},
        )
        conversation = Conversation(
            el_client,
            ctx.elevenlabs_agent_id,
            user_id=f"web_{ctx.agent_id}",
            requires_auth=bool(api_key),
            audio_interface=audio_if,
            config=config,
            callback_agent_response=_make_on_agent_response(websocket, loop),
            callback_user_transcript=_make_on_user_transcript(websocket, loop),
        )
        await asyncio.to_thread(conversation.start_session)
        await asyncio.sleep(0.5)
        await websocket.send_json({
            "type": "conversation_ready",
            "message": "Conversation ready",
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        return conversation
    except Exception as e:
        logger.exception("ElevenLabs conversation start failed: %s", e)
        await websocket.send_json({"type": "error", "message": str(e)})
        return None


def _is_minute_limit_exceeded(ctx: WebAgentContext) -> bool:
    if ctx.minute_limit is None:
        return False
    elapsed = (datetime.now(timezone.utc) - ctx.call_start_time).total_seconds() / 60
    return (ctx.initial_usage + elapsed) >= ctx.minute_limit


async def run_web_agent_session(
    websocket: WebSocket,
    ctx: WebAgentContext,
) -> Optional[str]:
    """
    Main message loop for the web agent WebSocket session.
    Handles conversation_init, user_audio_chunk, end, and minute-limit checks.
    Returns the ElevenLabs conversation_id (or None) after session ends.
    """
    loop = asyncio.get_running_loop()
    call_id = f"web_{ctx.agent_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    audio_if = BrowserAudioInterface(websocket, loop, call_id)

    conversation = None
    conversation_ready = False
    chunk_count = 0

    while True:
        # Periodic minute-limit check
        chunk_count += 1
        if chunk_count % 10 == 0 and _is_minute_limit_exceeded(ctx):
            logger.warning("Auto-disconnect user %s: monthly minutes limit", ctx.user_id)
            await websocket.send_json({
                "type": "error",
                "message": "Monthly minutes limit reached. Call disconnected.",
            })
            await websocket.close(code=1008)
            break

        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            break
        except Exception:
            continue

        msg_type = data.get("type")

        if msg_type == "conversation_init" and not conversation_ready:
            conversation = await _start_elevenlabs_conversation(
                websocket=websocket,
                audio_if=audio_if,
                ctx=ctx,
                language=data.get("language", "en"),
                model=data.get("model", "eleven_turbo_v2"),
            )
            if conversation is None:
                break
            conversation_ready = True

        elif msg_type == "user_audio_chunk" and conversation_ready:
            b64 = data.get("data_b64")
            if b64:
                try:
                    audio_if.push_user_audio(base64.b64decode(b64))
                except Exception as e:
                    logger.debug("Audio decode error: %s", e)

        elif msg_type == "end":
            break

    # End session and capture conversation_id
    conv_id: Optional[str] = None
    if conversation:
        try:
            conversation.end_session()
            conversation.wait_for_session_end()
            conv_id = conversation._conversation_id
            if conv_id:
                logger.info("Captured conversation_id: %s", conv_id)
        except Exception:
            logger.error("Error ending ElevenLabs session:\n%s", traceback.format_exc())

    return conv_id


# ─────────────────────────────────────────────────────────────────────────────
# Post-call storage helpers
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_cost(raw_el_cost: float) -> int:
    """Must be called inside db() context."""
    settings = CoinUsageSettingsModel.get_settings()
    return int((raw_el_cost * settings.elevenlabs_multiplier) + settings.static_conversation_cost)


def _persist_web_conversation(
    ctx: WebAgentContext,
    metadata: dict,
    conv_id: str,
    lead_id: Optional[int],
) -> ConversationsModel:
    """
    Saves conversation, deducts coins, links lead if present.
    Must be called inside db() context.

    force=True is passed to deduct_coins so that if the call cost exceeded
    the user's balance (overdraft), the full cost is still recorded and the
    balance goes negative rather than silently skipping the deduction.
    """
    calculated_cost = _calculate_cost(float(metadata.get("cost") or 0))
    call_status = CallStatusEnum.success if metadata.get("call_successful") else CallStatusEnum.failed

    record = ConversationsModel(
        agent_id=ctx.agent_id,
        user_id=ctx.user_id,
        message_count=metadata.get("message_count"),
        duration=metadata.get("duration"),
        call_status=call_status,
        channel=ChannelEnum.widget,
        transcript_summary=metadata.get("transcript_summary"),
        elevenlabs_conv_id=conv_id,
        cost=calculated_cost,
    )
    db.session.add(record)
    db.session.flush()

    if calculated_cost > 0:
        deduct_coins(
            user_id=ctx.user_id,
            amount=calculated_cost,
            reference_type="conversation",
            reference_id=record.id,
            commit=False,
            force=True,  # call already happened — always deduct full cost
        )

    db.session.commit()
    db.session.refresh(record)

    if lead_id:
        lead = db.session.query(WebAgentLeadModel).get(lead_id)
        if lead:
            lead.conversation_id = record.id
            db.session.add(lead)
            db.session.commit()
            logger.info("Linked lead %s to conversation %s", lead_id, record.id)

    return record


def _fetch_owner_notification_settings(user_id: int, lead_id: Optional[int]) -> tuple[OwnerNotificationSettings, str]:
    """
    Fetches owner notification prefs and lead name.
    Must be called inside db() context.
    Returns (OwnerNotificationSettings, lead_name).
    """
    settings = OwnerNotificationSettings(email=None, name="User", email_notifications=False, usage_alerts=False)
    lead_name = "Anonymous"

    owner = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == user_id).first()
    if owner:
        settings.email = owner.email
        settings.name = owner.first_name or owner.name or "User"
        if owner.notification_settings:
            settings.email_notifications = owner.notification_settings.email_notifications
            settings.usage_alerts = owner.notification_settings.useage_alerts

    if lead_id:
        lead = db.session.query(WebAgentLeadModel).get(lead_id)
        if lead and lead.name:
            lead_name = lead.name

    return settings, lead_name


async def maybe_send_notifications(
    ctx: WebAgentContext,
    record: ConversationsModel,
    metadata: dict,
    lead_id: Optional[int],
) -> None:
    """Sends conversation notification and low-coins alert emails if enabled."""
    with db():
        notif, lead_name = _fetch_owner_notification_settings(ctx.user_id, lead_id)
        current_balance = get_user_coin_balance(ctx.user_id)

    if notif.email and notif.email_notifications:
        try:
            await send_conversation_notification_email(
                company_email=notif.email,
                agent_name=ctx.web_agent_name,
                conversation_id=str(record.id),
                base_url=VoiceSettings.FRONTEND_URL,
                user_name=lead_name,
                summary=metadata.get("transcript_summary"),
                occurred_at=datetime.now(timezone.utc),
            )
            logger.info("Conversation notification sent to %s", notif.email)
        except Exception:
            logger.error("Failed to send conversation email:\n%s", traceback.format_exc())

    if notif.email and notif.usage_alerts and current_balance <= 1000:
        try:
            await send_low_coins_email(
                user_email=notif.email,
                current_coins=current_balance,
                base_url=VoiceSettings.FRONTEND_URL,
                user_name=notif.name,
            )
            logger.info("Low coins email sent to %s (balance=%s)", notif.email, current_balance)
        except Exception:
            logger.error("Failed to send low coins email:\n%s", traceback.format_exc())


async def save_web_conversation(
    ctx: WebAgentContext,
    conv_id: str,
    lead_id: Optional[int],
) -> None:
    """
    Fetches ElevenLabs metadata, persists record, deducts coins,
    links lead, and dispatches notification emails.
    """
    try:
        el_conv = ElevenLabsConversation()
        metadata = await asyncio.to_thread(el_conv.extract_conversation_metadata, conv_id)

        if not metadata:
            logger.error("Metadata extraction failed for conversation %s", conv_id)
            return

        with db():
            record = _persist_web_conversation(ctx, metadata, conv_id, lead_id)

        logger.info(
            "Conversation %s saved (duration=%ss, messages=%s, cost=%s)",
            conv_id, metadata.get("duration"), metadata.get("message_count"), record.cost,
        )

        await maybe_send_notifications(ctx, record, metadata, lead_id)

    except Exception:
        logger.error("save_web_conversation failed:\n%s", traceback.format_exc())


# ─────────────────────────────────────────────────────────────────────────────
# Activity logging helpers
# ─────────────────────────────────────────────────────────────────────────────

def log_web_chat_started(ctx: WebAgentContext, lead_id: Optional[int]) -> None:
    with db():
        log_activity(
            user_id=ctx.user_id,
            event_type="web_agent_chat_started",
            description=f"Public web chat started for agent: {ctx.agent_name}",
            metadata={
                "public_id": ctx.public_id,
                "agent_id": ctx.agent_id,
                "agent_name": ctx.agent_name,
                "web_agent_name": ctx.web_agent_name,
                "lead_id": lead_id,
            },
        )


def log_web_chat_ended(ctx: WebAgentContext, conv_id: Optional[str], lead_id: Optional[int]) -> None:
    with db():
        log_activity(
            user_id=ctx.user_id,
            event_type="web_agent_chat_ended",
            description=f"Public web chat ended for agent: {ctx.agent_name}",
            metadata={
                "public_id": ctx.public_id,
                "agent_id": ctx.agent_id,
                "agent_name": ctx.agent_name,
                "web_agent_name": ctx.web_agent_name,
                "conversation_id": conv_id,
                "lead_id": lead_id,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Embed script
# ─────────────────────────────────────────────────────────────────────────────

def _build_embed_script(public_id: str) -> str:
    """Returns the full widget + WebSocket client JS for a given public_id."""
    return r"""
(function() {
  var publicId = '%s';
  var script = document.currentScript;
  var baseUrl;
  if (script && script.src) {
    var u = new URL(script.src);
    baseUrl = u.origin + u.pathname.replace(/\/embed\.js\/[^\/]+$/, '');
  } else {
    baseUrl = window.location.origin + '/api/v2/web-agent';
  }

  var wsUrl     = (baseUrl.startsWith('https') ? 'wss:' : 'ws:') + baseUrl.split('://')[1] + '/ws/' + publicId;
  var configUrl = baseUrl + '/config/' + publicId;
  var leadUrl   = baseUrl + '/lead/'   + publicId;
  var logoUrl   = baseUrl + '/logo.svg';

  window.voiceNinjaPublicId = publicId;
  window.voiceNinjaWsUrl    = wsUrl;

  var vnStyles = '<style id="vn-widget-styles">' +
    '.vn-root{font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,\'Helvetica Neue\',sans-serif;display:flex;flex-direction:column;align-items:center;}' +
    '#vn-indicator-wrap{width:auto;height:48px;border-radius:24px;display:flex;align-items:center;justify-content:center;gap:0px;padding:0 12px;background:transparent;border:1px solid transparent;cursor:pointer;transition:width 0.35s cubic-bezier(.4,0,.2,1),border-radius 0.35s cubic-bezier(.4,0,.2,1),padding 0.35s cubic-bezier(.4,0,.2,1),gap 0.3s ease,box-shadow 0.35s ease,background 0.3s ease,border-color 0.3s ease;overflow:hidden;box-shadow:none;}' +
    '#vn-indicator-wrap:hover{width:130px;border-radius:26px;padding:0 14px;gap:10px;background:linear-gradient(145deg,#fef8f6 0%%,#f6f4ff 100%%);border-color:rgba(86,44,124,0.08);box-shadow:0 6px 24px rgba(86,44,124,0.18);}' +
    '#vn-indicator-wrap.vn-active,#vn-indicator-wrap.vn-connecting{width:130px;border-radius:26px;padding:0 14px;gap:10px;background:linear-gradient(145deg,#fef8f6 0%%,#f6f4ff 100%%);border-color:rgba(86,44,124,0.08);}' +
    '#vn-indicator-wrap.vn-active{box-shadow:0 0 20px rgba(224,105,67,0.35);}' +
    '#vn-indicator-wrap.vn-active:hover{box-shadow:0 0 24px rgba(220,50,50,0.40);}' +
    '#vn-indicator-wrap .vn-end-hint{display:none;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%%;background:rgba(220,50,50,0.15);color:#dc3232;font-size:11px;font-weight:700;flex-shrink:0;line-height:1;transition:background 0.2s ease;}' +
    '#vn-indicator-wrap.vn-active:hover .vn-end-hint{display:flex;}' +
    '#vn-indicator-wrap.vn-active:hover .vn-end-hint:hover{background:rgba(220,50,50,0.25);}' +
    '#vn-indicator-wrap.vn-connecting{animation:vn-pulse 1.8s ease-in-out infinite;}' +
    '@keyframes vn-pulse{0%%,100%%{box-shadow:0 0 12px rgba(86,44,124,0.15);}50%%{box-shadow:0 0 24px rgba(86,44,124,0.35);}}' +
    '#vn-indicator-wrap .vn-logo{height:28px;width:auto;object-fit:contain;display:block;flex-shrink:0;}' +
    '#vn-indicator-wrap .vn-voice-bars{display:flex;align-items:flex-end;gap:3px;height:16px;max-width:0;overflow:hidden;transition:max-width 0.3s cubic-bezier(.4,0,.2,1);}' +
    '#vn-indicator-wrap:hover .vn-voice-bars,#vn-indicator-wrap.vn-active .vn-voice-bars,#vn-indicator-wrap.vn-connecting .vn-voice-bars{max-width:50px;}' +
    '#vn-indicator-wrap .vn-voice-bars span{width:4px;border-radius:2px;background:linear-gradient(180deg,#E06943,#562C7C);height:4px;opacity:0;transition:height 0.15s ease,opacity 0.25s ease;}' +
    '#vn-indicator-wrap .vn-voice-bars span:nth-child(1){transition-delay:0s;}' +
    '#vn-indicator-wrap .vn-voice-bars span:nth-child(2){transition-delay:0.05s;}' +
    '#vn-indicator-wrap .vn-voice-bars span:nth-child(3){transition-delay:0.1s;}' +
    '#vn-indicator-wrap .vn-voice-bars span:nth-child(4){transition-delay:0.15s;}' +
    '#vn-indicator-wrap:hover .vn-voice-bars span,#vn-indicator-wrap.vn-active .vn-voice-bars span,#vn-indicator-wrap.vn-connecting .vn-voice-bars span{opacity:0.4;}' +
    '#vn-indicator-wrap:hover .vn-voice-bars span{animation:vn-bounce 0.35s ease;}' +
    '@keyframes vn-bounce{0%%{height:4px;}40%%{height:8px;}100%%{height:4px;}}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(1){animation:vn-bar 0.55s ease-in-out 0s infinite alternate;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(2){animation:vn-bar 0.55s ease-in-out 0.12s infinite alternate;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(3){animation:vn-bar 0.55s ease-in-out 0.24s infinite alternate;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(4){animation:vn-bar 0.55s ease-in-out 0.36s infinite alternate;}' +
    '@keyframes vn-bar{from{height:4px;}to{height:16px;}}' +
    '#vn-indicator-wrap.vn-connecting .vn-voice-bars span:nth-child(1){animation:vn-bar 1.2s ease-in-out 0s infinite alternate;}' +
    '#vn-indicator-wrap.vn-connecting .vn-voice-bars span:nth-child(2){animation:vn-bar 1.2s ease-in-out 0.2s infinite alternate;}' +
    '#vn-indicator-wrap.vn-connecting .vn-voice-bars span:nth-child(3){animation:vn-bar 1.2s ease-in-out 0.4s infinite alternate;}' +
    '#vn-indicator-wrap.vn-connecting .vn-voice-bars span:nth-child(4){animation:vn-bar 1.2s ease-in-out 0.6s infinite alternate;}' +
    '#vn-prechat-card{position:absolute;background:#fff;border-radius:16px;padding:20px;min-width:260px;box-shadow:0 10px 40px rgba(86,44,124,0.14),0 2px 12px rgba(0,0,0,0.06);border:1px solid rgba(224,105,67,0.08);transform:scale(0.92);opacity:0;pointer-events:none;transition:transform 0.25s cubic-bezier(.4,0,.2,1),opacity 0.25s ease;}' +
    '#vn-prechat-card.vn-show{transform:scale(1);opacity:1;pointer-events:auto;}' +
    '#vn-prechat-card .vn-prechat-title{font-weight:700;font-size:16px;color:#1e293b;line-height:1.2;margin-bottom:4px;}' +
    '#vn-prechat-card .vn-prechat-subtitle{font-size:12px;color:#64748b;line-height:1.4;margin-bottom:12px;}' +
    '#vn-prechat input{width:100%%;padding:10px;margin-bottom:8px;border:1px solid #ddd;border-radius:8px;box-sizing:border-box;font-size:14px;}' +
    '#vn-start-prechat{width:100%%;background:#562C7C;color:#fff;border:none;padding:10px;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;}' +
    '#vn-status-toast{position:absolute;background:#1e293b;color:#fff;font-size:12px;padding:8px 16px;border-radius:10px;white-space:nowrap;transform:scale(0.92);opacity:0;pointer-events:none;transition:transform 0.25s cubic-bezier(.4,0,.2,1),opacity 0.25s ease;}' +
    '#vn-status-toast.vn-show{transform:scale(1);opacity:1;pointer-events:auto;}' +
    '#vn-branding{font-size:8px;text-align:center;margin-top:6px;opacity:0.4;white-space:nowrap;transition:opacity 0.3s ease;}' +
    '</style>';

  var config = null;

  function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  async function init() {
    try {
      var resp = await fetch(configUrl);
      config = await resp.json();
      window.voiceNinjaLeadId = localStorage.getItem('vn_lead_' + publicId);
      injectWidget();
    } catch (e) {
      console.error('Voice Ninja init failed:', e);
    }
  }

  function injectWidget() {
    if (document.getElementById('voice-ninja-widget')) return;

    var pos = config.appearance.position || 'bottom-right';
    var posStyles    = pos === 'bottom-right' ? 'bottom:24px;right:24px;'
                     : pos === 'bottom-left'  ? 'bottom:24px;left:24px;'
                     : pos === 'top-right'    ? 'top:24px;right:24px;'
                     :                          'top:24px;left:24px;';
    var prechatPos   = (pos.indexOf('bottom') !== -1 ? 'bottom:58px;' : 'top:58px;') +
                       (pos.indexOf('right')  !== -1 ? 'right:0;'     : 'left:0;');

    var customFieldsHtml = '';
    (config.prechat.custom_fields || []).forEach(function(field) {
      var safeId = field.field_name.replace(/[^a-zA-Z0-9_-]/g, '_');
      customFieldsHtml +=
        '<input type="' + (field.field_type || 'text') + '" ' +
        'id="vn-custom-' + safeId + '" ' +
        'placeholder="' + escapeHtml(field.field_name) + '" ' +
        (field.required ? 'required' : '') + '>';
    });

    var div = document.createElement('div');
    div.id = 'voice-ninja-widget';
    div.innerHTML = vnStyles +
      '<div class="vn-root" style="position:fixed;' + posStyles + 'z-index:99999;">' +
        '<div id="vn-prechat-card" style="' + prechatPos + '">' +
          (config.appearance.widget_title    ? '<div class="vn-prechat-title">'    + escapeHtml(config.appearance.widget_title)    + '</div>' : '') +
          (config.appearance.widget_subtitle ? '<div class="vn-prechat-subtitle">' + escapeHtml(config.appearance.widget_subtitle) + '</div>' : '') +
          '<div id="vn-prechat">' +
            (config.prechat.require_name  ? '<input type="text"  id="vn-lead-name"  placeholder="Your Name">'     : '') +
            (config.prechat.require_email ? '<input type="email" id="vn-lead-email" placeholder="Email Address">' : '') +
            (config.prechat.require_phone ? '<input type="tel"   id="vn-lead-phone" placeholder="Phone Number">'  : '') +
            customFieldsHtml +
          '</div>' +
          '<button id="vn-start-prechat">Start Chat</button>' +
        '</div>' +
        '<div id="vn-status-toast" style="' + prechatPos + '"></div>' +
        '<div id="vn-indicator-wrap" title="Click to start voice chat">' +
          '<img class="vn-logo" src="' + logoUrl + '" alt="Voice Ninja"/>' +
          '<div class="vn-voice-bars"><span></span><span></span><span></span><span></span></div>' +
          '<div class="vn-end-hint">\u00d7</div>' +
        '</div>' +
        (config.appearance.show_branding ? '<div id="vn-branding">Powered by Voice Ninja</div>' : '') +
      '</div>';
    document.body.appendChild(div);

    if (config.appearance.primary_color) {
      var colorStyle = document.createElement('style');
      colorStyle.textContent =
        '#vn-indicator-wrap.vn-active{box-shadow:0 0 20px ' + config.appearance.primary_color + '66;}' +
        '#vn-indicator-wrap.vn-active:hover{box-shadow:0 0 24px ' + config.appearance.primary_color + '99;border-color:' + config.appearance.primary_color + '4D;}' +
        '#vn-start-prechat{background:' + config.appearance.primary_color + ';}';
      div.appendChild(colorStyle);
    }

    var pill          = document.getElementById('vn-indicator-wrap');
    var prechatCard   = document.getElementById('vn-prechat-card');
    var statusToast   = document.getElementById('vn-status-toast');
    var startBtn      = document.getElementById('vn-start-prechat');
    var connected     = false;
    var connecting    = false;
    var client        = null;
    var statusTimer   = null;

    div.querySelector('.vn-root').addEventListener('mouseleave', function() {
      if (!connected && !connecting) prechatCard.classList.remove('vn-show');
    });

    function showStatus(msg, duration) {
      statusToast.textContent = msg;
      statusToast.classList.add('vn-show');
      if (statusTimer) { clearTimeout(statusTimer); statusTimer = null; }
      if (duration > 0) statusTimer = setTimeout(function() { statusToast.classList.remove('vn-show'); }, duration);
    }
    function hideStatus() { statusToast.classList.remove('vn-show'); }
    function setState(state) {
      pill.classList.remove('vn-active', 'vn-connecting', 'vn-speaking');
      pill.title = 'Click to start voice chat';
      if (state === 'connecting') { pill.classList.add('vn-connecting'); pill.title = 'Connecting...'; }
      else if (state === 'active') { pill.classList.add('vn-active');    pill.title = 'Click to end call'; }
    }

    // ── VoiceNinjaClient ──────────────────────────────────────────────────────
    function VoiceNinjaClient(url) {
      this.wsUrl        = url;
      this.ws           = null;
      this.audioContext = null;
      this.mic          = null;
      this.processor    = null;
      this.audioReady   = false;
      this.SAMPLE_RATE  = 16000;
      this.audioQueue   = [];
      this.isPlaying    = false;
      this.currentSrc   = null;
    }

    VoiceNinjaClient.prototype.connect = function() {
      var self = this;
      return new Promise(function(resolve, reject) {
        self.ws = new WebSocket(self.wsUrl);
        self.ws.onopen = function() {
          self.ws.send(JSON.stringify({ type: 'conversation_init', language: 'en', model: 'eleven_turbo_v2' }));
          resolve();
        };
        self.ws.onmessage = function(ev) {
          try {
            var msg = JSON.parse(ev.data);
            if (msg.type === 'audio_interface_ready') { self.audioReady = true; if (self.audioContext) self.startStreaming(); }
            if (msg.type === 'audio_chunk' && msg.data_b64) {
              self.queuePlay(Uint8Array.from(atob(msg.data_b64), function(c) { return c.charCodeAt(0); }));
            }
          } catch (e) {}
        };
        self.ws.onclose = function() { connected = false; self.stopPlayback(); connecting = false; setState('idle'); showStatus('Disconnected', 3000); };
        self.ws.onerror = function() { reject(new Error('WebSocket error')); };
      });
    };

    VoiceNinjaClient.prototype.unlockAndStream = function() {
      var self = this;
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: this.SAMPLE_RATE });
      this.audioContext.resume().then(function() {
        navigator.mediaDevices.getUserMedia({ audio: { sampleRate: self.SAMPLE_RATE, channelCount: 1 } })
          .then(function(stream) { self.mic = stream; self.startStreaming(); })
          .catch(function() { showStatus('Microphone access denied', 4000); });
      });
    };

    VoiceNinjaClient.prototype.startStreaming = function() {
      if (!this.mic || !this.audioContext || !this.audioReady) return;
      var self = this;
      var src = this.audioContext.createMediaStreamSource(this.mic);
      this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);
      this.processor.onaudioprocess = function(ev) {
        if (!self.ws || self.ws.readyState !== 1) return;
        var input = ev.inputBuffer.getChannelData(0);
        var pcm   = new Int16Array(input.length);
        for (var i = 0; i < input.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, input[i] * 32767));
        self.ws.send(JSON.stringify({ type: 'user_audio_chunk', data_b64: btoa(String.fromCharCode.apply(null, new Uint8Array(pcm.buffer))) }));
      };
      src.connect(this.processor);
      this.processor.connect(this.audioContext.destination);
    };

    VoiceNinjaClient.prototype.queuePlay = function(buf) {
      this.audioQueue.push(buf);
      if (!this.isPlaying) this.playNext();
    };

    VoiceNinjaClient.prototype.playNext = function() {
      var wrap = document.getElementById('vn-indicator-wrap');
      if (!this.audioQueue.length) { this.isPlaying = false; this.currentSrc = null; if (wrap) wrap.classList.remove('vn-speaking'); return; }
      this.isPlaying = true;
      if (wrap) wrap.classList.add('vn-speaking');
      var self   = this;
      var int16  = new Int16Array((this.audioQueue.shift()).buffer);
      var f32    = new Float32Array(int16.length);
      for (var i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
      var ab  = this.audioContext.createBuffer(1, f32.length, this.SAMPLE_RATE);
      ab.getChannelData(0).set(f32);
      var src = this.audioContext.createBufferSource();
      src.buffer = ab;
      src.connect(this.audioContext.destination);
      src.onended = function() { self.currentSrc = null; setTimeout(function() { self.playNext(); }, 0); };
      this.currentSrc = src;
      src.start();
    };

    VoiceNinjaClient.prototype.stopPlayback = function() {
      this.audioQueue = [];
      if (this.currentSrc) { try { this.currentSrc.stop(); } catch (e) {} this.currentSrc = null; }
      this.isPlaying = false;
    };

    VoiceNinjaClient.prototype.disconnect = function() {
      this.stopPlayback();
      if (this.processor) try { this.processor.disconnect(); } catch (e) {}
      if (this.mic) this.mic.getTracks().forEach(function(t) { t.stop(); });
      if (this.ws) this.ws.close();
    };

    // ── Lead submission ───────────────────────────────────────────────────────
    async function submitLead() {
      var customData = (config.prechat.custom_fields || []).map(function(field) {
        var el = document.getElementById('vn-custom-' + field.field_name.replace(/[^a-zA-Z0-9_-]/g, '_'));
        return { field_name: field.field_name, field_type: field.field_type, value: el ? el.value : '' };
      });
      var resp = await fetch(leadUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name:        document.getElementById('vn-lead-name')  && document.getElementById('vn-lead-name').value,
          email:       document.getElementById('vn-lead-email') && document.getElementById('vn-lead-email').value,
          phone:       document.getElementById('vn-lead-phone') && document.getElementById('vn-lead-phone').value,
          custom_data: customData,
        }),
      }).then(function(r) { return r.json(); });
      if (resp && resp.id) { localStorage.setItem('vn_lead_' + publicId, resp.id); return resp; }
      return null;
    }

    // ── Pre-chat validation ───────────────────────────────────────────────────
    function validatePrechat() {
      if (config.prechat.require_name) {
        var el = document.getElementById('vn-lead-name');
        if (!el.value.trim()) { alert('Name is required'); el.focus(); return false; }
      }
      if (config.prechat.require_email) {
        var el = document.getElementById('vn-lead-email');
        if (!el.value.trim()) { alert('Email is required'); el.focus(); return false; }
      }
      if (config.prechat.require_phone) {
        var el  = document.getElementById('vn-lead-phone');
        var val = el.value.trim();
        if (!val) { alert('Phone is required'); el.focus(); return false; }
        if (!/^\+?[1-9]\d{1,14}$/.test(val.replace(/[\s\(\)\-\.]/g, ''))) { alert('Please enter a valid phone number'); el.focus(); return false; }
      }
      for (var i = 0; i < (config.prechat.custom_fields || []).length; i++) {
        var field = config.prechat.custom_fields[i];
        if (field.required) {
          var el = document.getElementById('vn-custom-' + field.field_name.replace(/[^a-zA-Z0-9_-]/g, '_'));
          if (el && !el.value.trim()) { alert(escapeHtml(field.field_name) + ' is required'); el.focus(); return false; }
        }
      }
      return true;
    }

    // ── Event listeners ───────────────────────────────────────────────────────
    pill.addEventListener('click', function() {
      if (connected || connecting) return;
      if (config.prechat.enable_prechat && !window.voiceNinjaLeadId) {
        prechatCard.classList.toggle('vn-show');
      } else {
        startCall();
      }
    });

    pill.querySelector('.vn-end-hint').addEventListener('click', function(e) {
      e.stopPropagation();
      if (connected && client) client.disconnect();
    });

    startBtn.addEventListener('click', async function() {
      if (!validatePrechat()) return;
      var resp = await submitLead();
      if (resp && resp.id) window.voiceNinjaLeadId = resp.id;
      prechatCard.classList.remove('vn-show');
      startCall();
    });

    function startCall() {
      connecting = true;
      setState('connecting');
      showStatus('Connecting...', 0);
      var url = wsUrl + (window.voiceNinjaLeadId ? '?lead_id=' + window.voiceNinjaLeadId : '');
      client = new VoiceNinjaClient(url);
      client.connect()
        .then(function() { connected = true; connecting = false; setState('active'); hideStatus(); client.unlockAndStream(); })
        .catch(function() { connecting = false; setState('idle'); showStatus('Connection failed', 4000); });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
""" % (public_id,)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/logo.svg", response_class=Response, summary="Voice Ninja logo")
async def logo_svg():
    return Response(
        VOICE_NINJA_LOGO_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/preview/{public_id}", response_class=HTMLResponse, summary="Preview page for web agent")
async def preview_page(request: Request, public_id: str):
    with db():
        web_agent = db.session.query(WebAgentModel).filter(WebAgentModel.public_id == public_id).first()
        if not web_agent:
            raise HTTPException(status_code=404, detail="Web Agent not found")
        if not web_agent.is_enabled:
            return HTMLResponse("<html><body><h1>Web Agent is disabled</h1></body></html>", status_code=403)
        web_agent_name = web_agent.web_agent_name

    base = str(request.base_url).rstrip("/")
    script_url = f"{base}/api/v2/web-agent/embed.js/{public_id}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Voice Ninja – {web_agent_name}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; min-height: 100vh; background: #f5f5f5; }}
    .header {{ padding: 16px 24px; background: #1a1a1a; color: #fff; }}
    .header h1 {{ margin: 0; font-size: 1.25rem; }}
  </style>
</head>
<body>
  <script src="{script_url}"></script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/embed.js/{public_id}", response_class=Response, summary="Embed script for web agent widget")
async def embed_script(public_id: str):
    # All ORM attribute access (including .agent relationship) must happen inside db()
    with db():
        web_agent = db.session.query(WebAgentModel).filter(WebAgentModel.public_id == public_id).first()
        if not web_agent:
            return Response("// Web Agent not found.", media_type="application/javascript", headers={"Cache-Control": "no-cache"})
        if not web_agent.is_enabled:
            return Response("// Web Agent is disabled.", media_type="application/javascript", headers={"Cache-Control": "no-cache"})
        if not web_agent.agent or not web_agent.agent.elevenlabs_agent_id:
            return Response("// Agent has no ElevenLabs configuration.", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    return Response(
        _build_embed_script(public_id),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.websocket("/ws/{public_id}")
async def web_agent_ws(websocket: WebSocket, public_id: str, lead_id: Optional[int] = None):
    """
    Pure orchestration — zero business logic lives here.

    Flow:
      1. Accept connection
      2. Validate web agent + owner limits
      3. Log chat start
      4. Run audio bridge session
      5. Log chat end
      6. Save conversation + notify
    """
    await websocket.accept()
    logger.info("Web agent WS connected for public_id=%s", public_id)

    # ── 1. Validate ───────────────────────────────────────────────────────────
    ctx = await fetch_and_validate_web_agent(websocket, public_id)
    if not ctx:
        return

    # ── 2. Log start ──────────────────────────────────────────────────────────
    log_web_chat_started(ctx, lead_id)

    # ── 3. Run session ────────────────────────────────────────────────────────
    conv_id = await run_web_agent_session(websocket, ctx)

    # ── 4. Log end ────────────────────────────────────────────────────────────
    log_web_chat_ended(ctx, conv_id, lead_id)

    # ── 5. Persist & notify ───────────────────────────────────────────────────
    if conv_id:
        await save_web_conversation(ctx, conv_id, lead_id)

    # ── 6. Close ──────────────────────────────────────────────────────────────
    try:
        await websocket.close()
    except Exception:
        pass

    logger.info("Web agent WS closed for public_id=%s", public_id)


@router.get("/config/{public_id}", response_model=WebAgentPublicConfig)
def get_public_config(public_id: str):
    web_agent = db.session.query(WebAgentModel).filter(WebAgentModel.public_id == public_id).first()
    if not web_agent:
        raise HTTPException(status_code=404, detail="Web Agent not found")
    if not web_agent.is_enabled:
        raise HTTPException(status_code=403, detail="Web Agent is disabled")

    return WebAgentPublicConfig(
        public_id=web_agent.public_id,
        web_agent_name=web_agent.web_agent_name,
        appearance={
            "widget_title":    web_agent.widget_title,
            "widget_subtitle": web_agent.widget_subtitle,
            "primary_color":   web_agent.primary_color,
            "position":        web_agent.position,
            "show_branding":   web_agent.show_branding,
        },
        prechat={
            "enable_prechat": web_agent.enable_prechat,
            "require_name":   web_agent.require_name,
            "require_email":  web_agent.require_email,
            "require_phone":  web_agent.require_phone,
            "custom_fields":  web_agent.custom_fields or [],
        },
    )


@router.post("/lead/{public_id}")
def submit_lead(public_id: str, lead: WebAgentLeadCreate):
    web_agent = db.session.query(WebAgentModel).filter(WebAgentModel.public_id == public_id).first()
    if not web_agent:
        raise HTTPException(status_code=404, detail="Web Agent not found")
    if not web_agent.is_enabled:
        raise HTTPException(status_code=403, detail="Web Agent is disabled")
    if not web_agent.enable_prechat:
        raise HTTPException(status_code=400, detail="Pre-chat is not enabled for this agent")

    new_lead = WebAgentLeadModel(
        web_agent_id=web_agent.id,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        custom_data=lead.custom_data,
    )
    db.session.add(new_lead)
    db.session.commit()
    db.session.refresh(new_lead)
    return {"detail": "Lead captured", "id": new_lead.id}