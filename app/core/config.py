from pydantic_settings import BaseSettings
from typing import List
from pathlib import Path
from functools import lru_cache
import os


os.makedirs("./audio_storage", exist_ok=True)

class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Voice Assistant"
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Google API Configuration
    GOOGLE_API_KEY: str

    # Database Configuration
    DB_URL: str
    
    # Audio Settings
    AUDIO_STORAGE_DIR: Path = Path("./audio_storage")
    MAX_AUDIO_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    ALLOWED_AUDIO_TYPES: List[str] = [".wav", ".mp3"]
    
    # Voice Configuration
    ALLOWED_VOICES: List[str] = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]
    DEFAULT_VOICE: str = "Charon"
    
    class Config:
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    return Settings()

VoiceSettings = get_settings()