from fastapi import FastAPI, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, FileResponse
from fastapi.security import HTTPBasic
import secrets
from typing import Dict
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from utils import AudioStorage, logger, CORS_SETTINGS, ALLOWED_VOICES, DEFAULT_VOICE, USERS, run_bot
from schemas.audio import (
    ErrorResponse, 
    SuccessResponse, 
    AudioFileResponse, 
    AudioFileListResponse
)
import uuid

templates = Jinja2Templates(directory="templates")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    **CORS_SETTINGS
)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("connect.html", {"request": request})



@app.get("/heartbeat")
async def heartbeat():
    logger.info("Heartbeat endpoint called")
    return JSONResponse(content={"message": "Voice Agent is running and ready to receive calls"})



# Define credentials store (replace with database in production)
USERS: Dict[str, str] = {
    "admin": "admin123",  # In production, store hashed passwords
}

security = HTTPBasic()

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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Get authentication header
    try:
        auth_header = websocket.query_params['authorization']
        voice = websocket.query_params.get('voice')
        if voice not in ALLOWED_VOICES:
            voice = DEFAULT_VOICE
        
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
            await run_bot(websocket, voice, uid)

        
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)


@app.get(
    "/audio/{session_id}",
    responses={
        200: {"model": AudioFileResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def get_audio_file(session_id: str):
    """Get audio file for a session"""
    try:
        audio_file = AudioStorage.get_audio_path(session_id)
        if not audio_file:
            return JSONResponse(
                status_code=404,
                content=ErrorResponse(error="Audio file not found").dict()
            )
        
        return FileResponse(
            path=audio_file,
            media_type="audio/wav",
            filename=audio_file.name,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'attachment; filename="{audio_file.name}"'
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error retrieving audio file: {str(e)}").dict()
        )


@app.get(
    "/audio",
    response_model=AudioFileListResponse,
    responses={
        500: {"model": ErrorResponse}
    }
)
async def get_audio_file_list(request: Request):
    try:
        audio_files = AudioStorage.get_audio_files()
        return AudioFileListResponse(
            audio_files=[
                AudioFileResponse(
                    filename=file.name,
                    session_id=file.stem,
                    file_url=str(request.url_for('get_audio_file', session_id=file.stem))
                ) for file in audio_files
            ]
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error retrieving audio files: {str(e)}").dict()
        )

@app.delete(
    "/audio/{session_id}",
    response_model=SuccessResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def delete_audio_file(session_id: str):
    """Delete audio file for session"""
    try:
        if AudioStorage.delete_audio(session_id):
            return SuccessResponse(message="Audio file deleted successfully")
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="Audio file not found").dict()
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error deleting audio file: {str(e)}").dict()
        )