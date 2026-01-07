"""API routers for app_v2."""

from app_v2.routers.otp import router as otp_router
from app_v2.routers.health import router as health_router
from app_v2.routers.register import router as register_router

__all__ = ['otp_router', 'health_router', 'register_router']

