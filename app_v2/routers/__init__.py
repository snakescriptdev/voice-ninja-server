"""API routers for app_v2."""

from app_v2.routers.otp import router as otp_router
from app_v2.routers.health import router as health_router
from app_v2.routers.google_auth import router as google_auth_router
from app_v2.routers.profile import router as profile_router
from .agents import router as agent_router
from .ai_model import router as ai_model_router
from .language import router as lang_router
from .voice import router as voice_router
from .functions import router as function_router
from .knowledge_base import router as knowledge_base_router
from .phone_router import router as phone_router
from .web_agent import router as web_agent_router
from .websocket_router import router as websocket_router


__all__ = ['otp_router', 'health_router', 'google_auth_router', 'profile_router', "agent_router", "ai_model_router", "lang_router", "voice_router", "function_router", "knowledge_base_router", "phone_router", "web_agent_router", "websocket_router"]

