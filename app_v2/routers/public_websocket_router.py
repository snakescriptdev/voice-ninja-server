import json
import base64
import asyncio
import traceback
import os
from datetime import datetime
from typing import Optional

import aiohttp
import bcrypt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi_sqlalchemy import db

from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY
from app_v2.databases.models import AgentModel, APIKeyModel, ConversationsModel, CallStatusEnum, ChannelEnum, CoinUsageSettingsModel
from app_v2.utils.coin_utils import deduct_coins, get_user_coin_balance
from app_v2.utils.activity_logger import log_activity
from app_v2.utils.feature_access import check_feature_limit_and_usage, get_feature_limit, get_feature_usage
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
from app_v2.core.logger import setup_logger

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

    with db():
        api_key_record = db.session.query(APIKeyModel).filter(APIKeyModel.client_id == client_id, APIKeyModel.is_active == True).first()
        if not api_key_record:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Client ID or inactive key")
            return
        
        # Verify secret
        if not bcrypt.checkpw(client_secret.encode('utf-8'), api_key_record.client_secret_hash.encode('utf-8')):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Client Secret")
            return
        
        user_id = api_key_record.user_id

        # 2. Verify agent ownership and configuration
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.user_id == user_id).first()
        if not agent or not agent.elevenlabs_agent_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Agent not found or not configured")
            return
        
        elevenlabs_agent_id = agent.elevenlabs_agent_id
        agent_name = agent.agent_name

        # 3. Check Balance and Limits
        user_balance = get_user_coin_balance(user_id)
        if user_balance <= 0:
            await websocket.send_json({"type": "error", "message": "Insufficient coins", "code": 1008})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Insufficient coins")
            return
        
        try:
            check_feature_limit_and_usage(user_id, "monthly_minutes")
        except Exception as e:
            await websocket.send_json({"type": "error", "message": str(e), "code": 1008})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Limit reached")
            return

    # Auth successful
    await websocket.send_json({
        "type": "status",
        "message": "Authenticated successfully",
        "ts": datetime.utcnow().isoformat()
    })
    logger.info(f"Public WebSocket authenticated for user {user_id}, agent {agent_id}")

    log_activity(
        user_id=user_id,
        event_type="public_agent_conversation_started",
        description=f"Started public voice chat for agent: {agent_name}",
        metadata={"agent_id": agent_id, "agent_name": agent_name, "elevenlabs_agent_id": elevenlabs_agent_id}
    )

    elevenlabs_ws_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={elevenlabs_agent_id}"
    call_start_time = datetime.now()
    initial_usage = get_feature_usage(user_id, "monthly_minutes")
    minute_limit = get_feature_limit(user_id, "monthly_minutes")
    conversation_id = None

    async with aiohttp.ClientSession() as session:
        if not ELEVENLABS_API_KEY:
            logger.error("ELEVENLABS_API_KEY is missing!")
            await websocket.send_json({"type": "error", "message": "Server configuration error", "code": 1011})
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return

        try:
            async with session.ws_connect(elevenlabs_ws_url, headers={"xi-api-key": ELEVENLABS_API_KEY}) as el_ws:
                logger.info(f"Connected to ElevenLabs WebSocket for agent {elevenlabs_agent_id}")
                
                async def browser_to_elevenlabs():
                    chunk_count = 0
                    try:
                        while True:
                            # Periodically check limit
                            if chunk_count % 10 == 0:
                                current_call_minutes = (datetime.now() - call_start_time).total_seconds() / 60
                                if minute_limit is not None and (initial_usage + current_call_minutes) >= minute_limit:
                                    await websocket.send_json({
                                        "type": "error",
                                        "message": "Monthly minutes limit reached. Call disconnected."
                                    })
                                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                                    return

                            message = await websocket.receive()
                            if message["type"] == "websocket.receive":
                                if "bytes" in message:
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
                    except Exception as e:
                        logger.error(f"Error in public_browser_to_elevenlabs: {e}")
                    finally:
                        if not el_ws.closed:
                            await el_ws.close()

                async def elevenlabs_to_browser():
                    nonlocal conversation_id
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
                                        "ts": datetime.utcnow().isoformat()
                                    })

                                if etype == "audio":
                                    audio_b64 = data.get("audio_event", {}).get("audio_base_64")
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
                                        "ts": datetime.utcnow().isoformat()
                                    })
                                elif etype == "agent_response":
                                    await websocket.send_json({
                                        "type": "agent_response",
                                        "text": data.get("agent_response_event", {}).get("agent_response"),
                                        "ts": datetime.utcnow().isoformat()
                                    })
                                else:
                                    # Forward all other events
                                    await websocket.send_json(data)
                            
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break
                    except Exception as e:
                        logger.error(f"Error in public_elevenlabs_to_browser: {e}")
                    finally:
                        try:
                            await websocket.close()
                        except RuntimeError:
                            pass

                # Run both tasks concurrently
                await asyncio.gather(
                    browser_to_elevenlabs(),
                    elevenlabs_to_browser(),
                    return_exceptions=True
                )

        except Exception as e:
            logger.error(f"ElevenLabs connection failed: {e}")
            await websocket.send_json({"type": "error", "message": "Failed to connect to voice engine", "code": 1011})
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

    # Post-conversation logic
    if conversation_id:
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
                return

            call_status_enum = CallStatusEnum.success if metadata.get("call_successful") else CallStatusEnum.failed

            with db():
                cost_data = metadata.get("cost")
                settings = CoinUsageSettingsModel.get_settings()
                raw_el_cost = float(cost_data) if cost_data else 0
                calculated_cost = int((raw_el_cost * settings.elevenlabs_multiplier) + settings.static_conversation_cost)

                conversation_data = ConversationsModel(
                    agent_id=agent_id,
                    user_id=user_id,
                    message_count=metadata.get("message_count"),
                    duration=metadata.get("duration"),
                    call_status=call_status_enum,
                    channel=ChannelEnum.api,
                    transcript_summary=metadata.get("transcript_summary"),
                    elevenlabs_conv_id=conversation_id,
                    cost=calculated_cost
                )

                db.session.add(conversation_data)
                db.session.flush()

                if calculated_cost > 0:
                    deduct_coins(
                        user_id=user_id, 
                        amount=calculated_cost, 
                        reference_type="api_conversation", 
                        reference_id=conversation_data.id, 
                        commit=False
                    )

                db.session.commit()
                db.session.refresh(conversation_data)

            logger.info(
                f"✅ Public Conversation {conversation_id} stored successfully "
                f"(cost={calculated_cost})"
            )

        except Exception:
            logger.error(f"Error while saving public WS conversation:\n{traceback.format_exc()}")
