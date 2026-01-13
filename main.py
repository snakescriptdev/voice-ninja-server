from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import app_v2 routers
# Note: These imports currently depend on 'app' which has been moved to 'archive'
# You may need to update the imports or move necessary dependencies back.
try:
    from app_v2.routers import otp_router, health_router, google_auth_router
except ImportError as e:
    print(f"Warning: Failed to import app_v2 routers: {e}")
    otp_router = None
    health_router = None
    google_auth_router = None

app = FastAPI(title="Voice Ninja V2 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include app_v2 routers if they were imported successfully
if otp_router:
    app.include_router(otp_router)
if health_router:
    app.include_router(health_router)
if google_auth_router:
    app.include_router(google_auth_router)

@app.get("/")
async def root():
    return {"message": "Voice Ninja V2 API is running", "app_v2_status": "active"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
