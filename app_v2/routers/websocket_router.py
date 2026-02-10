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
    token: str = Query(...),
):
    """
    WebSocket endpoint for testing an ElevenLabs agent.
    Relays PCM 16k audio between browser and ElevenLabs.
    """
    # 1. Authenticate user from query token
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if not user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION,reason="Invalid token")
            return
    except JWTError as e:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION,reason=f"Invalid token:{str(e)}")
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
        return

    await websocket.accept()
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
