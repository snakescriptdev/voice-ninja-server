from fastapi import WebSocket, status, APIRouter
from app.core import logger, VoiceSettings
from app.services import RunAssistant
from typing import Dict
import secrets,uuid

router = APIRouter(prefix="/ws")


# Define credentials store (replace with database in production)
USERS: Dict[str, str] = {
    "admin": "admin123",  # In production, store hashed passwords
}

# Verify credentials
async def verify_credentials(credentials: str) -> bool:
    try:
        # Decode base64 credentials from WebSocket
        import base64
        decoded = base64.b64decode(credentials).decode('utf-8')
        username, password = decoded.split(':')
        
        if username in USERS and secrets.compare_digest(
            USERS[username].encode('utf-8'),
            password.encode('utf-8')
        ):
            logger.info(f"Successful authentication attempt for user: {username}")
            return True
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        return False
    logger.warning(f"Failed authentication attempt for user: {username}")
    return False


@router.websocket("/voices/")
async def websocket_endpoint(websocket: WebSocket):
    # Get authentication header
    try:
        auth_header = websocket.query_params['authorization']
        voice = websocket.query_params.get('voice')
        if voice not in VoiceSettings.ALLOWED_VOICES:
            voice = VoiceSettings.DEFAULT_VOICE
        
        if not auth_header.startswith('Basic '):
            logger.warning("Missing or invalid Authorization header")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        credentials = auth_header.split(' ')[1]
        if not await verify_credentials(credentials):
            logger.warning("Invalid credentials provided")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        else:
            logger.info("Authentication successful")
            await websocket.accept()
            uid = uuid.uuid4()
            json_data = {
                "type": "UID",
                "uid": str(uid)
            }
            await websocket.send_json(json_data)
            await RunAssistant(websocket, voice, uid)

        
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
