from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .routers import APISRouter, WebRouter, WebSocketRouter, AdminRouter
from fastapi_sqlalchemy import DBSessionMiddleware,db
from app.core.config import VoiceSettings
from starlette.middleware.sessions import SessionMiddleware
import os
from config import MEDIA_DIR 
from app.databases.models import AdminTokenModel, TokensToConsume

app = FastAPI()

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
    engine_args={"pool_pre_ping": True, "pool_size": 20, "max_overflow": 0}
)

app.add_middleware(SessionMiddleware, secret_key=VoiceSettings.SECRET_KEY)

security = HTTPBasic()

app.include_router(APISRouter)
app.include_router(WebRouter)
app.include_router(WebSocketRouter)

@app.on_event("startup")
async def startup_event():
    # Ensure default models exists
    AdminTokenModel.ensure_default_exists()
    TokensToConsume.ensure_default_exists()

    
app.include_router(AdminRouter)