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

    # Cal API Configuration
    CAL_API_KEY: str

    # Database Configuration
    DB_URL: str
    
    # Audio Settings
    AUDIO_STORAGE_DIR: Path = Path("./audio_storage")
    MAX_AUDIO_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    ALLOWED_AUDIO_TYPES: List[str] = [".wav", ".mp3"]
    
    # Voice Configuration
    ALLOWED_VOICES: List[str] = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]
    DEFAULT_VOICE: str = "Charon"

    # Mail Configuration
    MAIL_USERNAME: str
    MAIL_PASSWORD: str
    MAIL_PORT: int
    MAIL_SERVER: str
    MAIL_TLS: bool
    MAIL_SSL: bool
    MAIL_FROM: str
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    NGROK_BASE_URL: str 
    RAZOR_KEY_ID: str
    RAZOR_KEY_SECRET: str
    DOMAIN_NAME: str
    HOST: str
    class Config:
        env_file = os.path.join(os.path.dirname(__file__), ".env")  # Absolute path
        env_file_encoding = "utf-8"  # Ensure proper encoding

@lru_cache()
def get_settings() -> Settings:
    return Settings()

VoiceSettings = get_settings()