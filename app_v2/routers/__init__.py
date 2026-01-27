"""API routers for app_v2."""

from app_v2.routers.otp import router as otp_router
from app_v2.routers.health import router as health_router
from app_v2.routers.google_auth import router as google_auth_router
from app_v2.routers.profile import router as profile_router
from app_v2.routers.langauge import router as lang_router
from app_v2.routers.ai_model import router as ai_model_router

__all__ = ['otp_router', 'health_router', 'google_auth_router', 'profile_router',"lang_router","ai_model_router"]

