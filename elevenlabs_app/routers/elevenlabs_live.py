import asyncio
import base64
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from loguru import logger
import os
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, AudioInterface, ConversationInitiationData
from app.databases.models import AgentModel
from elevenlabs_app.services.elevenlabs_post_call_recorder import elevenlabs_post_call_recorder
from elevenlabs_app.services.conversation_storage import elevenlabs_conversation_storage
from dotenv import load_dotenv
load_dotenv()


ElevenLabsLiveRouter = APIRouter(prefix="/elevenlabs/live", tags=["elevenlabs-live"]) 

# Prevent multiple concurrent sessions per agent dynamic id
ACTIVE_SESSIONS = {}


def handle_agent_response_live(call_id: str, response: str, websocket: WebSocket, loop: asyncio.AbstractEventLoop):
    """Handle agent response for live browser sessions"""
    try:
        logger.info(f"🤖 Agent response for call_id {call_id}: {response[:100]}{'...' if len(response) > 100 else ''}")
        
        # Send to browser
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({"type": "agent_response", "text": response, "ts": datetime.utcnow().isoformat()}),
            loop,
        )
        
    except Exception as e:
        logger.error(f"❌ Error handling agent response for call_id {call_id}: {e}", exc_info=True)


def handle_user_transcript_live(call_id: str, transcript: str, websocket: WebSocket, loop: asyncio.AbstractEventLoop):
    """Handle user transcript for live browser sessions"""
    try:
        logger.info(f"🎤 User transcript for call_id {call_id}: {transcript[:100]}{'...' if len(transcript) > 100 else ''}")
        
        # Send to browser
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({"type": "user_transcript", "text": transcript, "ts": datetime.utcnow().isoformat()}),
            loop,
        )
        
    except Exception as e:
        logger.error(f"❌ Error handling user transcript for call_id {call_id}: {e}", exc_info=True)


def handle_latency_measurement_live(call_id: str, latency_ms: float, websocket: WebSocket, loop: asyncio.AbstractEventLoop):
    """Handle latency measurement for live browser sessions"""
    try:
        logger.info(f"⏱️ Latency measurement for call_id {call_id}: {latency_ms}ms")
        
        # Send to browser
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({"type": "latency_measurement", "latency_ms": latency_ms, "ts": datetime.utcnow().isoformat()}),
            loop,
        )
        
    except Exception as e:
        logger.error(f"❌ Error handling latency measurement for call_id {call_id}: {e}", exc_info=True)


@ElevenLabsLiveRouter.get("/health")
async def health():
    api_key = os.getenv("ELEVENLABS_API_KEY")
    return {"status": "healthy", "requires_auth": bool(api_key)}


class BrowserAudioInterface(AudioInterface):
    """
    Bridges ElevenLabs Conversation audio with a browser WebSocket.
    - output(audio): send PCM s16le 16k mono chunks to browser as base64
    - start(input_callback): store callback and accept user mic chunks from WS
    - Includes call recording integration
    """

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop, call_id: str = None):
        self.websocket = websocket
        self.loop = loop
        self.call_id = call_id or f"call_{uuid.uuid4().hex[:12]}"
        self._input_cb = None
        self._started = False
        self.recording_enabled = True

    def start(self, input_callback):
        self._input_cb = input_callback
        self._started = True
        logger.info(f"✅ BrowserAudioInterface started by ElevenLabs for call_id: {self.call_id}")
        # Send signal to browser that we're truly ready
        try:
            if self.websocket.client_state.name == "CONNECTED":
                message = {
                    "type": "audio_interface_ready",
                    "message": "Audio interface is now active",
                    "ts": datetime.utcnow().isoformat()
                }
                asyncio.run_coroutine_threadsafe(self.websocket.send_json(message), self.loop)
        except Exception as e:
            logger.error(f"Error sending audio_interface_ready signal: {e}")

    def stop(self):
        self._started = False
        logger.info(f"BrowserAudioInterface stopped for call_id: {self.call_id}")

    def output(self, audio: bytes):
        try:
            if self.websocket.client_state.name == "CONNECTED":
                # Record agent audio if recording is enabled
                if self.recording_enabled:
                    # Audio is recorded by ElevenLabs directly
                    pass
                
                message = {
                    "type": "audio_chunk",
                    "sample_rate": 16000,
                    "channels": 1,
                    "format": "pcm_s16le",
                    "data_b64": base64.b64encode(audio).decode("ascii"),
                    "ts": datetime.utcnow().isoformat(),
                }
                asyncio.run_coroutine_threadsafe(self.websocket.send_json(message), self.loop)
        except Exception as e:
            logger.error(f"❌ Error sending audio to browser for call_id {self.call_id}: {e}")

    def interrupt(self):
        # Browser should stop playback locally
        pass

    # Helper to push user audio from browser to ElevenLabs
    def push_user_audio(self, audio: bytes):
        if self._started and self._input_cb:
            try:
                # Validate audio data
                if len(audio) == 0:
                    logger.warning(f"Empty audio data received for call_id {self.call_id}")
                    return
                
                # Record user audio if recording is enabled
                if self.recording_enabled:
                    # Audio is recorded by ElevenLabs directly
                    pass
                
                # Send audio to ElevenLabs
                self._input_cb(audio)
                # Removed verbose audio logging to reduce log noise
                # logger.debug(f"Successfully sent {len(audio)} bytes to ElevenLabs for call_id {self.call_id}")
            except Exception as e:
                logger.error(f"Error delivering browser audio to input_callback for call_id {self.call_id}: {e}")
                logger.error(f"Audio data length: {len(audio) if audio else 'None'}")
        else:
            logger.warning(f"Cannot push audio - started: {self._started}, callback: {bool(self._input_cb)} for call_id {self.call_id}")


@ElevenLabsLiveRouter.websocket("/ws/{agent_dynamic_id}")
async def live_ws(websocket: WebSocket, agent_dynamic_id: str):
    await websocket.accept()
    logger.info(f"🔌 Browser WebSocket connection accepted for agent: {agent_dynamic_id}")
    
    # Extract user_id from query parameters if provided
    query_params = dict(websocket.query_params)
    user_id = query_params.get('user_id')

    # Generate unique call ID for this session
    call_id = f"call_{agent_dynamic_id}_{uuid.uuid4().hex[:8]}"

    # Lookup ElevenLabs agent id from DB via dynamic_id
    agent: Optional[AgentModel] = AgentModel.get_by_dynamic_id(agent_dynamic_id)
    if not agent or not agent.elvn_lab_agent_id:
        await websocket.close(code=1003)
        raise HTTPException(status_code=404, detail="Agent or ElevenLabs agent_id not found")

    # Create user_id for ElevenLabs tracking
    elevenlabs_user_id = None
    if user_id:
        elevenlabs_user_id = f"user_{user_id}"
    elif agent.created_by:
        elevenlabs_user_id = f"agent_owner_{agent.created_by}"
    else:
        elevenlabs_user_id = f"anonymous_{call_id}"

    elevenlabs_agent_id = agent.elvn_lab_agent_id

    # Enhanced session management - close existing session if any
    if ACTIVE_SESSIONS.get(agent_dynamic_id):
        logger.warning(f"Existing session found for agent {agent_dynamic_id}, clearing it")
        ACTIVE_SESSIONS.pop(agent_dynamic_id, None)
        
        # Send notification about session replacement
        await websocket.send_json({
            "type": "session_replaced",
            "message": "Previous session replaced by new connection",
        })

    # Init ElevenLabs conversation
    loop = asyncio.get_running_loop()
    audio_if = BrowserAudioInterface(websocket, loop, call_id)
    conversation = None

    # Call metadata for recording
    call_metadata = {
        "agent_dynamic_id": agent_dynamic_id,
        "elevenlabs_agent_id": elevenlabs_agent_id,
        "call_type": "browser_live",
        "start_time": datetime.utcnow().isoformat()
    }

    try:
        ACTIVE_SESSIONS[agent_dynamic_id] = True
        
        # Track session metadata for post-call retrieval
        session_metadata = {
            'platform': 'browser',
            'timestamp': datetime.now().isoformat(),
            'agent_dynamic_id': agent_dynamic_id,
            'elevenlabs_user_id': elevenlabs_user_id,
            'query_user_id': user_id
        }
        
        # Register session for post-call data retrieval
        elevenlabs_post_call_recorder.register_conversation_session(
            call_id=call_id,
            agent_dynamic_id=agent_dynamic_id,
            elevenlabs_agent_id=elevenlabs_agent_id,
            metadata=session_metadata
        )
        
        api_key = os.getenv("ELEVENLABS_API_KEY")
        client = ElevenLabs(api_key=api_key if api_key else None)

        # Preflight: validate agent id format and auth against ElevenLabs
        if not elevenlabs_agent_id or not str(elevenlabs_agent_id).startswith("agent_"):
            await websocket.send_json({"type": "error", "message": "Invalid ElevenLabs agent_id"})
            await websocket.close()
            return

        if api_key:
            try:
                # Will raise if key lacks permission or agent is private/inaccessible
                _ = client.conversational_ai.conversations.get_signed_url(agent_id=elevenlabs_agent_id)
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": "ElevenLabs auth failed for this agent. Check API key/org access.",
                })
                await websocket.close()
                return
        
        # Create conversation initiation data with user_id and dynamic variables
        # Get the agent's selected ElevenLabs model
        selected_model = "eleven_turbo_v2"  # Default fallback
        try:
            # Try to access the relationship directly
            if hasattr(agent, 'selected_model_obj') and agent.selected_model_obj:
                selected_model = agent.selected_model_obj.name
                logger.info(f"📝 Agent {agent_dynamic_id} has selected model: {selected_model}")
            else:
                logger.warning(f"⚠️ Agent {agent_dynamic_id} has no selected model, using default: {selected_model}")
        except Exception as e:
            logger.warning(f"⚠️ Could not access agent model relationship: {e}, using default: {selected_model}")

        # Use SDK's ConversationInitiationData with proper configuration
        conversation_config = ConversationInitiationData(
            user_id=elevenlabs_user_id,
            conversation_config_override={
                "agent": {
                    "language": "en"  # Default language, will be overridden by client
                }
            },
            extra_body={
                "model": selected_model  # Model goes here
            },
            dynamic_variables={
                "user_id": elevenlabs_user_id,
                "call_id": call_id,
                "agent_dynamic_id": agent_dynamic_id,
                "client_type": "browser_live",
                "session_start": datetime.utcnow().isoformat()
            }
        )

        # Create ElevenLabs conversation with proper config
        conversation = Conversation(
            client,
            elevenlabs_agent_id,
            user_id=elevenlabs_user_id,
            requires_auth=bool(api_key),
            audio_interface=audio_if,
            config=conversation_config,  # Pass config to SDK
            callback_agent_response=lambda r: handle_agent_response_live(call_id, r, websocket, loop),
            callback_user_transcript=lambda t: handle_user_transcript_live(call_id, t, websocket, loop),
            callback_latency_measurement=lambda latency_ms: handle_latency_measurement_live(call_id, latency_ms, websocket, loop),
        )

        # Create initial call record in database
        try:
            call_record = elevenlabs_conversation_storage.create_call_record(
                agent_id=agent.id,
                conversation_id=call_id,  # Using our call_id as conversation identifier
                user_id=agent.created_by,  # Pass the actual agent owner's user_id to database
                session_metadata={
                    "elevenlabs_agent_id": elevenlabs_agent_id,
                    "elevenlabs_user_id": elevenlabs_user_id,  # ElevenLabs user identifier
                    "query_user_id": user_id,  # Original user_id from query params
                    "platform": "elevenlabs_live",
                    "client_ip": websocket.client.host if websocket.client else "unknown",
                    "selected_language": "en",  # Default, will be updated when client sends init
                    "selected_model": selected_model  # Include selected model in metadata
                }
            )
            if call_record:
                logger.info(f"📞 Created call record in database for call_id: {call_id}, user: {elevenlabs_user_id}, record_id: {call_record.id}")
            else:
                logger.error(f"❌ Call record creation returned None for call_id: {call_id}")
        except Exception as e:
            logger.error(f"❌ Failed to create call record: {e}", exc_info=True)
            # Continue anyway - don't fail the conversation

        # Start the conversation session
        logger.info(f"🚀 Starting ElevenLabs conversation session for call_id: {call_id}, user: {elevenlabs_user_id}")
        conversation.start_session()
        
        # Wait for the conversation to be fully initialized
        logger.info(f"⏳ Waiting for ElevenLabs conversation to initialize for call_id: {call_id}")
        await asyncio.sleep(0.5)  # Increased delay
        
        # Check if audio interface has been started by ElevenLabs
        if not audio_if._started:
            logger.warning(f"Audio interface not started after delay for call_id: {call_id}")
        
        # Send ready signal to browser
        logger.info(f"Sending conversation_ready signal for call_id: {call_id}")
        await websocket.send_json({
            "type": "conversation_ready",
            "message": "ElevenLabs conversation is ready",
            "ts": datetime.utcnow().isoformat()
        })

        # Receive mic audio from browser
        audio_chunk_count = 0
        
        while True:
            try:
                data = await websocket.receive_json()
                
            except WebSocketDisconnect:
                break
            except Exception:
                # Non-JSON messages are ignored
                continue

            msg_type = data.get("type")
            if msg_type == "conversation_init":
                # Handle language and model override from client
                selected_language = data.get("language", "en")
                selected_model_override = data.get("model")

                logger.info(f"📝 Received conversation_init: language={selected_language}, model={selected_model_override} for call_id: {call_id}")
                
                # Send contextual update with the new language preference
                if conversation:
                    try:
                        # Send contextual update with the new language preference
                        context_message = f"User has changed language preference to {selected_language}"
                        if selected_model_override:
                            context_message += f" and model to {selected_model_override}"

                        conversation.send_contextual_update(context_message)
                        logger.info(f"📤 Sent contextual update for configuration change: {context_message}")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to send contextual update: {e}")

                # Update call record with selected language and model
                metadata_update = {"selected_language": selected_language}
                if selected_model_override:
                    metadata_update["selected_model"] = selected_model_override

                try:
                    elevenlabs_conversation_storage.update_call_metadata(
                        conversation_id=call_id,
                        metadata=metadata_update
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Failed to update call metadata: {e}")

                # Send confirmation back to client
                confirmation_data = {
                    "type": "language_confirmed",
                    "language": selected_language,
                    "message": f"Language set to {selected_language}",
                    "ts": datetime.utcnow().isoformat()
                }

                if selected_model_override:
                    confirmation_data.update({
                        "model": selected_model_override,
                        "model_message": f"Model set to {selected_model_override}"
                    })

                await websocket.send_json(confirmation_data)
                
            elif msg_type == "user_audio_chunk":
                b64 = data.get("data_b64")
                if not b64:
                    continue
                try:
                    audio_bytes = base64.b64decode(b64)
                    # Validate audio data format
                    if len(audio_bytes) == 0:
                        logger.warning(f"Empty audio chunk received for call_id {call_id}")
                        continue
                    
                    # Process audio chunk
                    audio_chunk_count += 1
                    
                    audio_if.push_user_audio(audio_bytes)
                except Exception as e:
                    logger.error(f"❌ Error processing user audio chunk for call_id {call_id}: {e}", exc_info=True)
            elif msg_type == "end":
                break
            else:
                # Log unknown message types for debugging
                
                pass

    except Exception as e:
        logger.error(f"Live WS error: {e}")
    finally:
        try:
            if conversation:
                conversation.end_session()
                conversation.wait_for_session_end()
        except Exception:
            pass
        
        # Mark conversation as ended for post-call retrieval
        try:
            # Get the ElevenLabs conversation ID if available
            elevenlabs_conversation_id = None
            if conversation and hasattr(conversation, 'conversation_id'):
                elevenlabs_conversation_id = conversation.conversation_id
            
            # Update call status in database
            try:
                elevenlabs_conversation_storage.update_call_status(
                    conversation_id=call_id,
                    status="completed",
                    end_metadata={
                        "elevenlabs_conversation_id": elevenlabs_conversation_id,
                        "end_reason": "normal_disconnect"
                    }
                )
                logger.info(f"📞 Updated call status to completed for call_id: {call_id}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to update call status: {e}")
            
            # Mark conversation as ended to trigger post-call data retrieval
            elevenlabs_post_call_recorder.mark_conversation_ended(
                call_id=call_id,
                elevenlabs_conversation_id=elevenlabs_conversation_id
            )
            logger.info(f"Post-call data retrieval marked for call_id: {call_id}")
        except Exception as e:
            logger.error(f"Error marking conversation ended for call_id {call_id}: {e}")
        
        # Clean up active session
        try:
            ACTIVE_SESSIONS.pop(agent_dynamic_id, None)
            logger.info(f"Cleaned up active session for agent: {agent_dynamic_id}")
        except Exception as e:
            logger.error(f"Error cleaning up active session for {agent_dynamic_id}: {e}")
        
        # Close WebSocket only if it's still open
        try:
            if websocket.client_state.name != "DISCONNECTED":
                await websocket.close()
                logger.info("Live stream socket closed")
            else:
                logger.info("Live stream socket already closed")
        except Exception as e:
            logger.warning(f"Error closing WebSocket (probably already closed): {e}")