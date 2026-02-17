from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from fastapi.responses import HTMLResponse
from fastapi_sqlalchemy import db
from app_v2.databases.models import AgentModel
import json
import base64
import aiohttp
import asyncio
import traceback
import os
from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY
from app_v2.utils.jwt_utils import SECRET_KEY, ALGORITHM
from jose import jwt, JWTError
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/agent",
    tags=["websocket"],
)

@router.get("/test-page", response_class=HTMLResponse)
async def get_test_page():
    """
    Serves the test agent HTML page.
    """
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "agent_test.html")
    with open(template_path, "r") as f:
        return f.read()

@router.websocket("/{agent_id}/test-connection")
async def websocket_test_agent(
    websocket: WebSocket,
    agent_id: int,
):
    """
    WebSocket endpoint for testing an ElevenLabs agent.
    Relays PCM 16k audio between browser and ElevenLabs.
    """

    # 0. Accept connection first (required for first-message auth)
    await websocket.accept()

    # 1. ---- FIRST MESSAGE AUTH ----
    try:
        auth_msg = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=5
        )
    except asyncio.TimeoutError:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Auth timeout"
        )
        logger.error(f"Auth timeout for agent {agent_id}")
        return

    if auth_msg.get("type") != "auth" or "token" not in auth_msg:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Auth required"
        )
        logger.error(f"Auth required for agent {agent_id}")
        return

    token = auth_msg["token"]

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if not user_id:
            raise JWTError("user_id missing")
    except JWTError:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid token"
        )
        logger.error(f"Invalid token for user {user_id}")
        return

    # 2. Verify agent ownership
    with db():
        agent = (
            db.session.query(AgentModel)
            .filter(AgentModel.id == agent_id, AgentModel.user_id == user_id)
            .first()
        )
        if agent:
             elevenlabs_agent_id = agent.elevenlabs_agent_id
        else:
             elevenlabs_agent_id = None

    if not elevenlabs_agent_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION,reason="Agent not found")
        logger.error(f"Agent not found for user {user_id}")
        return

    # await websocket.accept()
    logger.info(f"Accepted WebSocket connection for agent {agent_id} (EL: {elevenlabs_agent_id})")

    elevenlabs_ws_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={elevenlabs_agent_id}"

    async with aiohttp.ClientSession() as session:
        if not ELEVENLABS_API_KEY:
            logger.error("ELEVENLABS_API_KEY is missing!")
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR,reason="ELEVENLABS_API_KEY is missing!")
            return

        async with session.ws_connect(elevenlabs_ws_url, headers={"xi-api-key": ELEVENLABS_API_KEY}) as el_ws:
            logger.info(f"Connected to ElevenLabs WebSocket for agent {elevenlabs_agent_id}")
            
            chunk_count = 0
            async def browser_to_elevenlabs():
                nonlocal chunk_count
                logger.info("Starting browser_to_elevenlabs loop")
                try:
                    while True:
                        message = await websocket.receive()
                        if "bytes" in message:
                            chunk_count += 1
                            if chunk_count % 100 == 0:
                                logger.info(f"Relayed {chunk_count} chunks to ElevenLabs")
                            # Send audio chunk to ElevenLabs
                            audio_b64 = base64.b64encode(message["bytes"]).decode("utf-8")
                            await el_ws.send_json({"user_audio_chunk": audio_b64})
                        elif "text" in message:
                            logger.info(f"Browser text: {message['text']}")
                            data = json.loads(message["text"])
                            await el_ws.send_json(data)
                        elif message["type"] == "websocket.disconnect":
                            logger.info(f"Browser sent disconnect message: {message.get('code')}")
                            break
                except WebSocketDisconnect:
                    logger.info("Browser disconnected (WebSocketDisconnect)")
                except Exception as e:
                    logger.error(f"Error in browser_to_elevenlabs:\n{traceback.format_exc()}")
                finally:
                   logger.info("browser_to_elevenlabs task finishing")
                   if not el_ws.closed:
                       await el_ws.close()

            async def elevenlabs_to_browser():
                logger.info("Starting elevenlabs_to_browser loop")
                try:
                    async for msg in el_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            # Relay ElevenLabs events to browser
                            etype = data.get("type")
                            
                            if etype == "audio":
                                # Handle audio payload key 'audio_base_64'
                                audio_b64 = data.get("audio_event", {}).get("audio_base_64")
                                if audio_b64:
                                    audio_bytes = base64.b64decode(audio_b64)
                                    await websocket.send_bytes(audio_bytes)
                                    
                                    # Forward metadata but strip large audio string
                                    data["audio_event"]["audio_base_64"] = "[STRIPPED]"
                                    await websocket.send_json(data)
                            else:
                                # Relay all other text data (metadata, transcripts, etc.)
                                await websocket.send_json(data)
                                if etype and etype != "ping":
                                    logger.info(f"Relayed event: {etype}")
                                
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error(f"ElevenLabs WS error: {el_ws.exception()}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                             logger.info("ElevenLabs WS closed by remote")
                             break
                except asyncio.CancelledError:
                    logger.info("elevenlabs_to_browser task cancelled")
                except Exception as e:
                    logger.error(f"Error in elevenlabs_to_browser:\n{traceback.format_exc()}")
                finally:
                    logger.info("elevenlabs_to_browser task finishing")
                    try:
                        await websocket.close()
                    except RuntimeError:
                        pass

            # Run both tasks concurrently
            tasks = [
                asyncio.create_task(browser_to_elevenlabs(), name="browser_task"),
                asyncio.create_task(elevenlabs_to_browser(), name="elevenlabs_task")
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            
            for task in pending:
                logger.info(f"Cancelling pending task: {task.get_name()}")
                task.cancel()
            
            logger.info("WebSocket connection flow completed")



@router.get("/{agent_id}/test-connection/info", tags=["WebSocket"])
def websocket_test_agent_info(agent_id: int):
    """
    Information for WebSocket test-connection endpoint.
    """
    return {
        "endpoint": f"/{agent_id}/test-connection",
        "method": "WEBSOCKET",
        "url_format": "ws://<host>/api/v2/agent/{agent_id}/test-connection",
        "authentication": {
            "type": "JWT",
            "mode": "first_message",
            "message_format": {
                "type": "auth",
                "token": "<JWT>"
            },
            "note": "JWT must contain `user_id`. Client must send auth message immediately after connection opens."
        },
        "description": (
            "This WebSocket establishes a bi-directional audio bridge between the browser "
            "and ElevenLabs Conversational AI. After connection, the client must first send "
            "an authentication message containing a JWT. The server validates the token, "
            "verifies agent ownership, and then relays PCM 16k audio in real time."
        ),
        "client_flow": [
            "1. Open WebSocket connection",
            "2. Send auth message as first JSON payload",
            "3. Start sending audio / events"
        ],
        "browser_to_server": {
            "auth": {
                "example": {
                    "type": "auth",
                    "token": "<JWT>"
                }
            },
            "audio": {
                "format": "raw bytes",
                "encoding": "PCM 16k",
                "direction": "Browser → ElevenLabs"
            },
            "json_events": {
                "example": {
                    "type": "start_conversation"
                }
            }
        },
        "server_to_browser": {
            "audio": {
                "format": "raw bytes",
                "encoding": "PCM 16k",
                "direction": "ElevenLabs → Browser"
            },
            "metadata": [
                "transcript",
                "agent_response",
                "conversation_events"
            ]
        },
        "timeouts": {
            "auth_timeout_seconds": 5,
            "note": "If auth message is not received within timeout, connection is closed"
        },
        "close_codes": {
            "1008": "Policy violation (auth failed / agent not found / auth timeout)",
            "1011": "Internal server error"
        }
    }
