from pathlib import Path

# Audio storage configuration
AUDIO_STORAGE_DIR = Path("audio_storage")
AUDIO_STORAGE_DIR.mkdir(exist_ok=True)

# Audio file settings
MAX_AUDIO_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_AUDIO_TYPES = [".wav", ".mp3"]
SAMPLE_RATE = 8000

# CORS Configuration
CORS_SETTINGS = {
    "allow_origins": ["*"],  # Allow all origins for testing
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}

# Voice Configuration
ALLOWED_VOICES = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]
DEFAULT_VOICE = "Charon"

# Authentication Configuration
USERS = {
    "admin": "admin123",  # In production, store hashed passwords
}