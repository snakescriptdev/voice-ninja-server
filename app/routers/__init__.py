from .api import router as APISRouter
from .web import router as WebRouter
# from .websocket import router as WebSocketRouter
from .adminpanel import router as AdminRouter

# __all__ = ["APISRouter", "WebRouter", "WebSocketRouter", "AdminRouter"]
__all__ = ["APISRouter", "WebRouter", "AdminRouter"]