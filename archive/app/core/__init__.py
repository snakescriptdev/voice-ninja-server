from .logger import setup_logger
from .config import VoiceSettings
logger = setup_logger(__name__)

__all__ = [
    "logger",
    "VoiceSettings"
]