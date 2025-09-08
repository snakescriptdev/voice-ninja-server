import asyncio
import base64
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from loguru import logger
import os
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, AudioInterface
from app.databases.models import AgentModel
from dotenv import load_dotenv
load_dotenv()


ElevenLabsLiveRouter = APIRouter(prefix="/elevenlabs/live", tags=["elevenlabs-live"]) 

# Prevent multiple concurrent sessions per agent dynamic id
ACTIVE_SESSIONS = {}


@ElevenLabsLiveRouter.get("/health")
async def health():
    api_key = os.getenv("ELEVENLABS_API_KEY")
    return {"status": "healthy", "requires_auth": bool(api_key)}


class BrowserAudioInterface(AudioInterface):
    """
    Bridges ElevenLabs Conversation audio with a browser WebSocket.
    - output(audio): send PCM s16le 16k mono chunks to browser as base64
    - start(input_callback): store callback and accept user mic chunks from WS
    """

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop):
        self.websocket = websocket
        self.loop = loop
        self._input_cb = None
        self._started = False

    def start(self, input_callback):
        self._input_cb = input_callback
        self._started = True
        logger.info("BrowserAudioInterface started")

    def stop(self):
        self._started = False
        logger.info("BrowserAudioInterface stopped")

    def output(self, audio: bytes):
        try:
            if self.websocket.client_state.name == "CONNECTED":
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
            logger.error(f"Error sending audio to browser: {e}")

    def interrupt(self):
        # Browser should stop playback locally
        pass

    # Helper to push user audio from browser to ElevenLabs
    def push_user_audio(self, audio: bytes):
        if self._started and self._input_cb:
            try:
                self._input_cb(audio)
            except Exception as e:
                logger.error(f"Error delivering browser audio to input_callback: {e}")


@ElevenLabsLiveRouter.websocket("/ws/{agent_dynamic_id}")
async def live_ws(websocket: WebSocket, agent_dynamic_id: str):
    await websocket.accept()
    logger.info(f"Browser connected for live stream: {agent_dynamic_id}")

    # Lookup ElevenLabs agent id from DB via dynamic_id
    agent: Optional[AgentModel] = AgentModel.get_by_dynamic_id(agent_dynamic_id)
    if not agent or not agent.elvn_lab_agent_id:
        await websocket.close(code=1003)
        raise HTTPException(status_code=404, detail="Agent or ElevenLabs agent_id not found")

    elevenlabs_agent_id = agent.elvn_lab_agent_id

    # reject if an active session already exists for this agent
    if ACTIVE_SESSIONS.get(agent_dynamic_id):
        await websocket.send_json({
            "type": "error",
            "message": "An active session already exists for this agent. Please stop the other preview first.",
        })
        await websocket.close()
        return

    # Init ElevenLabs conversation
    loop = asyncio.get_running_loop()
    audio_if = BrowserAudioInterface(websocket, loop)
    conversation = None

    try:
        ACTIVE_SESSIONS[agent_dynamic_id] = True
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
        conversation = Conversation(
            client,
            elevenlabs_agent_id,
            requires_auth=bool(api_key),
            audio_interface=audio_if,
            callback_agent_response=lambda r: asyncio.run_coroutine_threadsafe(
                websocket.send_json({"type": "agent_response", "text": r, "ts": datetime.utcnow().isoformat()}),
                loop,
            ),
            callback_user_transcript=lambda t: asyncio.run_coroutine_threadsafe(
                websocket.send_json({"type": "user_transcript", "text": t, "ts": datetime.utcnow().isoformat()}),
                loop,
            ),
        )

        conversation.start_session()

        # Receive mic audio from browser
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                # Non-JSON messages are ignored
                continue

            msg_type = data.get("type")
            if msg_type == "user_audio_chunk":
                b64 = data.get("data_b64")
                if not b64:
                    continue
                try:
                    audio_bytes = base64.b64decode(b64)
                    audio_if.push_user_audio(audio_bytes)
                except Exception as e:
                    logger.error(f"Failed to process user audio chunk: {e}")
            elif msg_type == "end":
                break
            else:
                # Ignore unknown message types
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
        ACTIVE_SESSIONS.pop(agent_dynamic_id, None)
        await websocket.close()
        logger.info("Live stream socket closed")


