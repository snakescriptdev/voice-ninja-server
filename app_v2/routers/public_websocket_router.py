import json
import base64
import asyncio
import traceback
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import bcrypt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi_sqlalchemy import db

from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY
from app_v2.databases.models import AgentModel, APIKeyModel, ChannelEnum, CoinUsageSettingsModel
from app_v2.utils.coin_utils import get_user_coin_balance
from app_v2.utils.activity_logger import log_activity
from app_v2.utils.feature_access import check_feature_limit_and_usage, get_feature_limit, get_feature_usage
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
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
from app_v2.utils.ws_call_log import start_ws_call_log, finalize_ws_call_log
from app_v2.utils.log_sanitizer import sanitize_for_log
from app_v2.schemas.enum_types import PublicLogChannelEnum
from app_v2.core.logger import setup_logger
from app_v2.routers.websocket_router import check_elevenlabs_credits

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/public",
    tags=["public-websocket"],
)

@router.websocket("/ws/{agent_id}")
async def public_websocket_agent(
    websocket: WebSocket,
    agent_id: int,
):
    """
    Public WebSocket endpoint for agents.
    Requires first-message authentication with API Key (client_id and client_secret).
    """
    await websocket.accept()
    logger.info(f"Public WebSocket connection attempt for agent {agent_id}")

    # 1. ---- FIRST MESSAGE AUTH ----
    try:
        auth_msg = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=5
        )
    except asyncio.TimeoutError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Auth timeout")
        return
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid auth message format")
        return
    
    if auth_msg.get("type") != "auth" or "client_id" not in auth_msg or "client_secret" not in auth_msg:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Auth required: client_id and client_secret")
        return
    
    client_id = auth_msg["client_id"]
    client_secret = auth_msg["client_secret"]

    ws_log_id = None
    with db():
        api_key_record = db.session.query(APIKeyModel).filter(APIKeyModel.client_id == client_id, APIKeyModel.is_active == True).first()
        if not api_key_record:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Client ID or inactive key")
            return

        # A user is attributable as soon as client_id resolves to a key, even
        # if the secret check below then fails — log from this point on.
        user_id = api_key_record.user_id
        ws_log_id = start_ws_call_log(
            user_id=user_id,
            channel=PublicLogChannelEnum.public_websocket,
            api_route="/api/v2/public/ws/{agent_id}",
            request_params={"path_params": {"agent_id": agent_id}},
            request_body=sanitize_for_log({
                "type": auth_msg.get("type"), "client_id": client_id, "client_secret": client_secret,
            }),
            api_key_id=api_key_record.id,
        )

        # Verify secret
        if not bcrypt.checkpw(client_secret.encode('utf-8'), api_key_record.client_secret_hash.encode('utf-8')):
            finalize_ws_call_log(ws_log_id, is_success=False, status_code=1008, error_message="Invalid Client Secret")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Client Secret")
            return

        # 2. Verify agent ownership and configuration
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.user_id == user_id).first()
        if not agent or not agent.elevenlabs_agent_id:
            finalize_ws_call_log(ws_log_id, is_success=False, status_code=1008, error_message="Agent not found or not configured")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Agent not found or not configured")
            return
        
        elevenlabs_agent_id = agent.elevenlabs_agent_id
        agent_name = agent.agent_name
        agent_llm_price = agent.llm_price_per_minute
        coin_settings = CoinUsageSettingsModel.get_settings()
        is_first_call = is_agents_first_call(agent_id)
        llm_cost_multiplier = resolve_llm_cost_multiplier(agent, coin_settings)
        llm_rate_basis = None if is_first_call else resolve_llm_rate_basis(agent)

        # 3. Check Balance and Limits
        user_balance = get_user_coin_balance(user_id)
        minimum_required = get_minimum_call_balance()
        if user_balance < minimum_required:
            finalize_ws_call_log(
                ws_log_id, is_success=False, status_code=1008,
                error_message=f"Insufficient coins. Minimum {minimum_required} coins required to start a call.",
            )
            await websocket.send_json({
                "type": "error",
                "message": f"Insufficient coins. Minimum {minimum_required} coins required to start a call.",
                "code": 1008,
            })
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Insufficient coins")
            return

        try:
            check_feature_limit_and_usage(user_id, "monthly_minutes")
        except Exception as e:
            conversation_row_id = start_conversation(user_id, agent_id, ChannelEnum.api)
            mark_conversation_failed(conversation_row_id, "Monthly minutes limit reached")
            finalize_ws_call_log(ws_log_id, is_success=False, status_code=1008, error_message="Monthly minutes limit reached")
            await websocket.send_json({"type": "error", "message": str(e), "code": 1008})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Limit reached")
            return

        has_credits = await check_elevenlabs_credits(websocket, user_id, agent_id, channel=ChannelEnum.api)
        if not has_credits:
            finalize_ws_call_log(ws_log_id, is_success=False, status_code=1008, error_message="ElevenLabs credits check failed")
            return


    # Auth successful
    await websocket.send_json({
        "type": "status",
        "message": "Authenticated successfully",
        "ts": datetime.now(timezone.utc).isoformat()
    })
    logger.info(f"Public WebSocket authenticated for user {user_id}, agent {agent_id}")

    with db():
        log_activity(
            user_id=user_id,
            event_type="public_agent_conversation_started",
            description=f"Started public voice chat for agent: {agent_name}",
            metadata={"agent_id": agent_id, "agent_name": agent_name, "elevenlabs_agent_id": elevenlabs_agent_id}
        )
        conversation_row_id = start_conversation(user_id, agent_id, ChannelEnum.api)

    elevenlabs_ws_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={elevenlabs_agent_id}"
    call_start_time = datetime.now(timezone.utc)
    initial_usage = get_feature_usage(user_id, "monthly_minutes")
    minute_limit = get_feature_limit(user_id, "monthly_minutes")
    conversation_id = None
    limit_reached = False
    low_balance_reached = False
    first_call_limit_reached = False

    async with aiohttp.ClientSession() as session:
        if not ELEVENLABS_API_KEY:
            logger.error("ELEVENLABS_API_KEY is missing!")
            with db():
                mark_conversation_failed(conversation_row_id, "Server configuration error: ELEVENLABS_API_KEY missing")
                finalize_ws_call_log(ws_log_id, is_success=False, status_code=1011, error_message="Server configuration error: ELEVENLABS_API_KEY missing")
            await websocket.send_json({"type": "error", "message": "Server configuration error", "code": 1011})
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return

        try:
            async with session.ws_connect(elevenlabs_ws_url, headers={"xi-api-key": ELEVENLABS_API_KEY}) as el_ws:
                logger.info(f"Connected to ElevenLabs WebSocket for agent {elevenlabs_agent_id}")
                
                async def browser_to_elevenlabs():
                    nonlocal limit_reached, low_balance_reached, first_call_limit_reached
                    chunk_count = 0
                    try:
                        while True:
                            # Periodically check limit
                            if chunk_count % 10 == 0:
                                current_call_minutes = (datetime.now(timezone.utc) - call_start_time).total_seconds() / 60
                                if minute_limit is not None and (initial_usage + current_call_minutes) >= minute_limit:
                                    limit_reached = True
                                    await websocket.send_json({
                                        "type": "error",
                                        "message": "Monthly minutes limit reached. Call disconnected."
                                    })
                                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                                    return

                                with db():
                                    low_balance = is_balance_exhausted(
                                        call_start_time,
                                        user_balance,
                                        agent_llm_price_per_minute=agent_llm_price,
                                        llm_cost_multiplier=llm_cost_multiplier,
                                        llm_rate_basis=llm_rate_basis,
                                    )
                                if low_balance:
                                    low_balance_reached = True
                                    await websocket.send_json({
                                        "type": "error",
                                        "message": "Low balance. Call ended to avoid exceeding your available credits."
                                    })
                                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                                    return

                                with db():
                                    first_call_capped = is_first_call_duration_exceeded(call_start_time, is_first_call)
                                if first_call_capped:
                                    first_call_limit_reached = True
                                    await websocket.send_json({
                                        "type": "error",
                                        "message": "First call duration limit reached. Call ended."
                                    })
                                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                                    return

                            message = await websocket.receive()
                            if message["type"] == "websocket.receive":
                                if "bytes" in message:
                                    if el_ws.closed:
                                        logger.info("ElevenLabs socket already closed; stopping browser relay")
                                        break
                                    chunk_count += 1
                                    audio_b64 = base64.b64encode(message["bytes"]).decode("utf-8")
                                    await el_ws.send_json({"user_audio_chunk": audio_b64})
                                elif "text" in message:
                                    data = json.loads(message["text"])
                                    # Handle specialized client-to-server messages if needed
                                    await el_ws.send_json(data)
                            elif message["type"] == "websocket.disconnect":
                                break
                    except WebSocketDisconnect:
                        pass
                    except ConnectionResetError:
                        # ElevenLabs closed its side (e.g. silence timeout) between our
                        # el_ws.closed check and the send — expected race, not an error.
                        logger.info("ElevenLabs connection reset while relaying audio (likely EL-side timeout)")
                    except Exception as e:
                        logger.error(f"Error in public_browser_to_elevenlabs: {e}")
                    finally:
                        if not el_ws.closed:
                            await el_ws.close()

                async def elevenlabs_to_browser():
                    nonlocal conversation_id
                    last_interrupt_id = 0
                    try:
                        async for msg in el_ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                etype = data.get("type")

                                if etype == "conversation_initiation_metadata":
                                    conversation_metadata = data.get("conversation_initiation_metadata_event")
                                    conversation_id = conversation_metadata.get("conversation_id")
                                    await websocket.send_json({
                                        "type": "status",
                                        "message": "Audio interface ready",
                                        "conversation_id": conversation_id,
                                        "ts": datetime.now(timezone.utc).isoformat()
                                    })

                                if etype == "interruption":
                                    last_interrupt_id = int(data.get("interruption_event", {}).get("event_id", 0))

                                if etype == "audio":
                                    audio_event = data.get("audio_event", {})
                                    # Audio already in flight when the user barges in keeps
                                    # arriving after the interruption event — drop anything
                                    # at or before the last interrupt so stale agent audio
                                    # doesn't get played back (and bleed into the mic) after
                                    # the agent was told to stop.
                                    if int(audio_event.get("event_id", 0)) <= last_interrupt_id:
                                        continue
                                    audio_b64 = audio_event.get("audio_base_64")
                                    if audio_b64:
                                        audio_bytes = base64.b64decode(audio_b64)
                                        await websocket.send_bytes(audio_bytes)
                                        # Also send metadata but strip audio
                                        data["audio_event"]["audio_base_64"] = "[STRIPPED]"
                                        await websocket.send_json(data)
                                elif etype == "user_transcript":
                                    await websocket.send_json({
                                        "type": "user_transcript",
                                        "text": data.get("user_transcript_event", {}).get("transcript"),
                                        "ts": datetime.now(timezone.utc).isoformat()
                                    })
                                elif etype == "agent_response":
                                    await websocket.send_json({
                                        "type": "agent_response",
                                        "text": data.get("agent_response_event", {}).get("agent_response"),
                                        "ts": datetime.now(timezone.utc).isoformat()
                                    })
                                else:
                                    # Forward all other events
                                    await websocket.send_json(data)

                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break

                        # Loop ended because EL's socket closed on its own (e.g. silence
                        # timeout) rather than this task being cancelled — tell the client
                        # explicitly instead of just dropping the connection.
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
                    except Exception as e:
                        logger.error(f"Error in public_elevenlabs_to_browser: {e}")
                    finally:
                        try:
                            await websocket.close()
                        except RuntimeError:
                            pass

                # Run both tasks concurrently; cancel whichever is still going
                # once the other finishes, so a call ending on either side
                # (EL hangup or browser disconnect) tears the whole bridge
                # down promptly instead of leaving the other task hanging.
                tasks = [
                    asyncio.create_task(browser_to_elevenlabs(), name="public_browser_task"),
                    asyncio.create_task(elevenlabs_to_browser(), name="public_elevenlabs_task"),
                ]
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        except Exception as e:
            logger.error(f"ElevenLabs connection failed: {e}")
            with db():
                mark_conversation_failed(conversation_row_id, "Failed to connect to voice engine")
                finalize_ws_call_log(ws_log_id, is_success=False, status_code=1011, error_message="Failed to connect to voice engine")
            await websocket.send_json({"type": "error", "message": "Failed to connect to voice engine", "code": 1011})
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

    if limit_reached:
        limit_error = "Monthly minutes limit reached"
    elif low_balance_reached:
        limit_error = LOW_BALANCE_ERROR_MESSAGE
    elif first_call_limit_reached:
        limit_error = FIRST_CALL_DURATION_LIMIT_ERROR_MESSAGE
    else:
        limit_error = None
    if limit_error:
        logger.warning(f"Call for user {user_id} ended: {limit_error}")

    # Post-conversation logic
    # Even when the monthly limit ended the call, a captured conversation_id
    # means real EL metadata exists — finalize normally (with the limit note
    # attached) so transcript/history still shows up, instead of discarding
    # it via mark_conversation_failed().
    if conversation_id:
        with db():
            log_activity(
                user_id=user_id,
                event_type="public_agent_conversation_completed",
                description=f"Completed public voice chat for agent: {agent_name}",
                metadata={"agent_id": agent_id, "conversation_id": conversation_id}
            )

        try:
            el_conv = ElevenLabsConversation()
            metadata = await asyncio.to_thread(
                el_conv.extract_conversation_metadata,
                conversation_id
            )

            if not metadata:
                logger.error(f"Metadata extraction failed for public WS conversation {conversation_id}")
                with db():
                    mark_conversation_failed(conversation_row_id, limit_error or "Metadata extraction failed")
                    finalize_ws_call_log(
                        ws_log_id, is_success=False, status_code=1011,
                        error_message=limit_error or "Metadata extraction failed",
                    )
                return

            with db():
                conversation_data = finalize_conversation(
                    conversation_row_id, metadata, conversation_id, reference_type="api_conversation",
                    error_message=limit_error,
                )
                finalize_ws_call_log(
                    ws_log_id,
                    is_success=not limit_error,
                    status_code=1000,
                    response_body=sanitize_for_log({"conversation_id": conversation_id, "cost": conversation_data.cost}),
                    error_message=limit_error,
                )

            logger.info(
                f"✅ Public Conversation {conversation_id} stored successfully "
                f"(cost={conversation_data.cost})"
            )

        except Exception:
            logger.error(f"Error while saving public WS conversation:\n{traceback.format_exc()}")
            with db():
                mark_conversation_failed(conversation_row_id, limit_error or "Failed to save conversation")
                finalize_ws_call_log(
                    ws_log_id, is_success=False, status_code=1011,
                    error_message=limit_error or "Failed to save conversation",
                )
    else:
        with db():
            mark_conversation_failed(conversation_row_id, limit_error or "No conversation ID captured")
            finalize_ws_call_log(
                ws_log_id, is_success=False, status_code=1011,
                error_message=limit_error or "No conversation ID captured",
            )
