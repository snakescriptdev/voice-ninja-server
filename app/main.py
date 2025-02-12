from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic
from fastapi.staticfiles import StaticFiles
from .routers import APISRouter, WebRouter, WebSocketRouter
from fastapi_sqlalchemy import DBSessionMiddleware,db
from app.core import VoiceSettings

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/audio", StaticFiles(directory="audio_storage"), name="audio")

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

security = HTTPBasic()

app.include_router(APISRouter)
app.include_router(WebRouter)
app.include_router(WebSocketRouter)