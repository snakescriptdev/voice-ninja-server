import json

# from bot import run_bot
from fastapi import FastAPI, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse,HTMLResponse
from fastapi.security import HTTPBasic
import secrets
from typing import Dict
from logger_setup import logger
from bot import run_bot
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

templates = Jinja2Templates(directory="templates")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        await run_bot(websocket)

        
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)