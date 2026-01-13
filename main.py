from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_sqlalchemy import DBSessionMiddleware, db
from app_v2.core.config import VoiceSettings
from starlette.middleware.sessions import SessionMiddleware
import os
from dotenv import load_dotenv
from app_v2.databases.models import AdminTokenModel, TokensToConsume, VoiceModel
from app_v2.routers import otp_router, health_router, google_auth_router

# Load environment variables
load_dotenv()

app = FastAPI(title="Voice Ninja V2 API", version="2.0.0")

# Add Middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

# Include app_v2 routers
app.include_router(otp_router)
app.include_router(health_router)
app.include_router(google_auth_router)

@app.get("/", tags=["System"])
async def root():
    return {"message": "Voice Ninja V2 API is running", "app_v2_status": "active"}

@app.on_event("startup")
async def startup_event():
    import asyncio
    
    async def init_background_tasks():
        # Ensure default models exists
        try:
            AdminTokenModel.ensure_default_exists()
            TokensToConsume.ensure_default_exists()
            VoiceModel.ensure_default_voices()
            print("🚀 Background initialization complete!")
        except Exception as e:
            print(f"❌ Initialization failed: {e}")
    
    asyncio.create_task(init_background_tasks())
    print("Voice Ninja V2 started successfully!")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
