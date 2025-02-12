from .api import router as APISRouter
from .web import router as WebRouter
from .websocket import router as WebSocketRouter

__all__ = ["APISRouter", "WebRouter", "WebSocketRouter"]