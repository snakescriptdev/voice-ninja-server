from pydantic_settings import BaseSettings
from typing import List
from pathlib import Path
from functools import lru_cache
import os

class Settings(BaseSettings):
    PROJECT_NAME: str = "Voice Ninja V2"
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 24 * 60  #1d 
    
    # Database Configuration
    DB_URL: str
    
    # Mail Configuration
    MAIL_USERNAME: str = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD")
    MAIL_FROM: str = os.getenv("MAIL_FROM")
    
    # Twilio Configuration
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER")
    
    # Google OAuth Configuration
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI")

    #GEMINI API Configuration
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    
    # ElevenLabs Configuration
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY")

    # Frontend Configuration
    FRONTEND_URL: str = os.getenv("FRONTEND_URL")

    #Ngrok base url
    NGROK_BASE_URL: str = os.getenv("NGROK_BASE_URL")

    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    return Settings()

VoiceSettings = get_settings()
