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
    
    # API Configuration
    GOOGLE_API_KEY: str
    GOOGLE_API_QUOTA_LIMIT: int = 1000  # Default quota limit
    GOOGLE_API_QUOTA_WINDOW: int = 3600  # 1 hour in seconds
    
    # Fallback Configuration
    ENABLE_FALLBACK_LLM: bool = False
    FALLBACK_LLM_API_KEY: str = ""
    FALLBACK_LLM_ENDPOINT: str = ""
    
    # Cal API Configuration
    CAL_API_KEY: str

    # Database Configuration
    DB_URL: str
    
    # Audio Settings
    AUDIO_STORAGE_DIR: Path = Path("./audio_storage")
    MAX_AUDIO_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    ALLOWED_AUDIO_TYPES: List[str] = [".wav", ".mp3"]
    
    # Audio Quality Configuration
    AUDIO_SAMPLE_RATE: int = 16000
    AUDIO_BUFFER_SIZE_MS: int = 200
    AUDIO_SMOOTHING_WINDOW_MS: int = 50
    AUDIO_SILENCE_THRESHOLD_MS: int = 500
    AUDIO_MAX_BUFFER_SIZE_MS: int = 1000
    AUDIO_DROP_THRESHOLD_MS: int = 100
    AUDIO_FADE_IN_MS: int = 30
    AUDIO_FADE_OUT_MS: int = 30
    AUDIO_CHANNELS: int = 1
    WEBSOCKET_BUFFER_SIZE: int = 262144
    WEBSOCKET_MAX_MESSAGE_SIZE: int = 8 * 1024 * 1024
    
    # Enhanced Audio Quality for Noisy Environments
    AUDIO_NOISE_REDUCTION_ENABLED: bool = True
    AUDIO_NOISE_REDUCTION_STRENGTH: float = 0.7  # 0.0 to 1.0
    AUDIO_ECHO_CANCELLATION: bool = True
    AUDIO_AGC_ENABLED: bool = True  # Automatic Gain Control
    AUDIO_AGC_TARGET_LEVEL: float = -20.0  # dB
    AUDIO_AGC_COMPRESSION_RATIO: float = 2.0
    AUDIO_HIGH_PASS_FILTER_FREQ: int = 80  # Hz - removes low frequency noise
    AUDIO_LOW_PASS_FILTER_FREQ: int = 8000  # Hz - removes high frequency noise
    
    # VAD (Voice Activity Detection) Optimization
    VAD_SENSITIVITY: float = 0.5  # 0.0 to 1.0 (lower = more sensitive)
    VAD_MIN_SPEECH_DURATION_MS: int = 250  # Minimum speech duration to trigger
    VAD_MAX_SPEECH_DURATION_MS: int = 30000  # Maximum speech duration
    VAD_SILENCE_DURATION_MS: int = 1000  # Silence duration to end speech
    VAD_PRE_SPEECH_PAD_MS: int = 100  # Audio padding before speech
    VAD_POST_SPEECH_PAD_MS: int = 100  # Audio padding after speech
    
    # Adaptive Buffer Management for Noisy Environments
    AUDIO_ADAPTIVE_BUFFERING: bool = True
    AUDIO_NOISE_LEVEL_THRESHOLD: float = -30.0  # dB - threshold to trigger noise handling
    AUDIO_BUFFER_SCALING_FACTOR: float = 1.5  # Increase buffer size in noisy environments
    AUDIO_MAX_NOISE_BUFFER_SIZE_MS: int = 1500  # Maximum buffer size when noise detected
    
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
        env_file = ".env"
        extra = "ignore"  # Ignore extra fields from environment variables

@lru_cache()
def get_settings() -> Settings:
    return Settings()

VoiceSettings = get_settings()