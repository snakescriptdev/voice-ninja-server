from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
# from app.routers import APISRouter, WebRouter,  AdminRouter ,WebSocketRouter
from app.routers import APISRouter, WebRouter, AdminRouter
from elevenlabs_app.routers import (
    ElevenLabsAPIRouter,
    ElevenLabsWebRouter,
    ElevenLabsWebSocketRouter,
    ElevenLabsLiveRouter,
)
from elevenlabs_app.routers.recording_api import ElevenLabsRecordingRouter
from elevenlabs_app.routers.web_integration import ElevenLabsWebRouter as ElevenLabsWebIntegrationRouter
from elevenlabs_app.services.elevenlabs_post_call_recorder import elevenlabs_post_call_recorder
from fastapi_sqlalchemy import DBSessionMiddleware,db
from app.core.config import VoiceSettings
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import os
from config import MEDIA_DIR 
from app.databases.models import AdminTokenModel, TokensToConsume, VoiceModel
from app_v2.routers import otp_router, health_router, register_router

app = FastAPI(title="Voice Ninja API Documentation", version="2.0.0")

# Ensure the media directory exists
os.makedirs(MEDIA_DIR, exist_ok=True)

from dotenv import load_dotenv
load_dotenv()  # Force load before Settings

# Custom static files handler with cache-busting
class NoCacheStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    async def __call__(self, scope, receive, send):
        response = await super().__call__(scope, receive, send)
        
        # Add cache-busting headers
        if hasattr(response, 'headers'):
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        
        return response

app.mount("/static", NoCacheStaticFiles(directory="static"), name="static")
app.mount("/media", NoCacheStaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/audio", NoCacheStaticFiles(directory="audio_storage"), name="audio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    DBSessionMiddleware,
    db_url=VoiceSettings.DB_URL,
    engine_args={"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}
)

app.add_middleware(SessionMiddleware, secret_key=VoiceSettings.SECRET_KEY)

security = HTTPBasic()

# Include Voice Ninja app routers (existing) - excluded from OpenAPI docs
app.include_router(APISRouter, prefix="", include_in_schema=False)
app.include_router(WebRouter, prefix="", include_in_schema=False)
# app.include_router(WebSocketRouter, prefix="/ws")
app.include_router(ElevenLabsWebSocketRouter, prefix="/ws", include_in_schema=False)
app.include_router(AdminRouter, prefix="/admin", include_in_schema=False)

# Include ElevenLabs Integration app routers (new) - excluded from OpenAPI docs
app.include_router(ElevenLabsAPIRouter, prefix="/elevenlabs/api/v1", include_in_schema=False)
app.include_router(ElevenLabsWebRouter, prefix="/elevenlabs/web/v1", include_in_schema=False)
# Live browser streaming WS
app.include_router(ElevenLabsLiveRouter, prefix="", include_in_schema=False)
# Recording management API
app.include_router(ElevenLabsRecordingRouter, prefix="", include_in_schema=False)
# Web integration (preview system) - using separate preview path to avoid conflicts
app.include_router(ElevenLabsWebIntegrationRouter, prefix="/elevenlabs/preview/v1", include_in_schema=False)
# app.include_router(ElevenLabsAdminRouter, prefix="/elevenlabs/admin")

# Include app_v2 routers (new refactored API) - shown in OpenAPI docs
app.include_router(otp_router)
app.include_router(health_router)
app.include_router(register_router)

@app.on_event("startup")
async def startup_event():
    # Start essential database models check in background
    import asyncio
    
    async def init_background_tasks():
        # Ensure default models exists
        AdminTokenModel.ensure_default_exists()
        TokensToConsume.ensure_default_exists()
        VoiceModel.ensure_default_voices()
        
        # Start ElevenLabs post-call recording service
        await elevenlabs_post_call_recorder.start_retrieval_service()
        
        print("🚀 Background initialization complete!")
    
    # Start background tasks without blocking startup
    asyncio.create_task(init_background_tasks())
    
    print("Voice Ninja started successfully!")

@app.on_event("shutdown")
async def shutdown_event():
    # Stop ElevenLabs post-call recording service
    await elevenlabs_post_call_recorder.stop_retrieval_service()
    print("Voice Ninja shutdown complete!")
