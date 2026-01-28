import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi_sqlalchemy import DBSessionMiddleware
from app_v2.dependecies import get_db
from sqlalchemy.orm import Session
from app_v2.core.config import VoiceSettings
from starlette.middleware.sessions import SessionMiddleware
from app_v2.databases.models.users import AdminTokenModel, TokensToConsume
from app_v2.databases.models.voices import VoiceModel
from app_v2.routers import otp_router, health_router, google_auth_router, profile_router, lang_router,ai_model_router,agent_router
from app_v2.utils.jwt_utils import HTTPBearer


from app_v2.databases.init_db import init__db


app = FastAPI(title="Voice Ninja V2 API", version="2.0.0")


@app.on_event("startup")
def startup():
    init__db()



# Global exception handler for Pydantic validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle Pydantic validation errors and return consistent error format."""
    # Extract the first error message
    errors = exc.errors()
    if errors:
        first_error = errors[0]
        # Get the error message
        error_msg = first_error.get('msg', 'Validation error')
        # Clean up the message - remove "Value error, " prefix if present
        if error_msg.startswith('Value error, '):
            error_msg = error_msg.replace('Value error, ', '')
        
        return JSONResponse(
            status_code=400,
            content={
                "detail": {
                    "message": error_msg,
                    "status": "failed",
                    "status_code": 400
                }
            }
        )
    
    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "message": "Validation error",
                "status": "failed",
                "status_code": 400
            }
        }
    )

# Global exception handler to ensure consistent error response structure
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle all HTTPExceptions and ensure consistent error response format."""
    # If detail is already a dict with the expected structure, use it
    if isinstance(exc.detail, dict):
        # Check if it has the expected keys
        if "message" in exc.detail and "status" in exc.detail and "status_code" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail}
            )
        # If it's a dict but not in the expected format, wrap it
        elif "status" in exc.detail and "message" in exc.detail:
            # It's in the old format without nested detail
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail}
            )
    
    # If detail is a string or other format, convert it to the expected structure
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": {
                "message": str(exc.detail) if not isinstance(exc.detail, dict) else exc.detail.get("message", "An error occurred"),
                "status": "failed",
                "status_code": exc.status_code
            }
        }
    )

# Security scheme for Bearer token
security = HTTPBearer()

# Custom OpenAPI function to add security scheme
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    # Get the default OpenAPI schema
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    # Add security scheme
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# Add security scheme to OpenAPI
app.openapi_components = {
    "securitySchemes": {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
}

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
app.include_router(profile_router)
app.include_router(lang_router)
app.include_router(ai_model_router)
app.include_router(agent_router)

@app.get("/", tags=["System"])
async def root():
    return {"message": "Voice Ninja V2 API is running", "app_v2_status": "active"}

@app.on_event("startup",)
async def startup_event():
    import asyncio
    
    async def init_background_tasks():
        # Ensure default models exists
        db: Session = next(get_db())
        try:
            AdminTokenModel.ensure_default_exists(db)
            TokensToConsume.ensure_default_exists(db)
            VoiceModel.ensure_default_voices(db)
            print("🚀 Background initialization complete!")
        except Exception as e:
            print(f"❌ Initialization failed: {e}")
    
    asyncio.create_task(init_background_tasks())
    print("Voice Ninja V2 started successfully!")




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app="main:app", host="127.0.0.1", port=8000, reload = True)
