from fastapi import FastAPI
from elevenlabs_app.routers.api import ElevenLabsAPIRouter
from elevenlabs_app.routers.elevenlabs_live import ElevenLabsLiveRouter
from elevenlabs_app.routers.recording_api import ElevenLabsRecordingRouter

def create_elevenlabs_app():
    """
    Create and configure the ElevenLabs FastAPI app with all routers
    """
    app = FastAPI(
        title="Voice Ninja - ElevenLabs Integration",
        description="ElevenLabs Conversational AI Integration with Call Recording and Twilio Support",
        version="1.0.0"
    )
    
    # Include all ElevenLabs routers
    app.include_router(ElevenLabsAPIRouter)
    app.include_router(ElevenLabsLiveRouter)
    app.include_router(ElevenLabsRecordingRouter)
    
    @app.get("/elevenlabs/health")
    async def health_check():
        """Overall health check for ElevenLabs integration"""
        import os
        return {
            "status": "healthy",
            "app": "Voice Ninja ElevenLabs Integration",
            "features": {
                "conversational_ai": True,
                "call_recording": True,
                "browser_live": True
            },
            "elevenlabs_api_configured": bool(os.getenv("ELEVENLABS_API_KEY"))
        }
    
    return app


# Create the app instance
elevenlabs_app = create_elevenlabs_app()
