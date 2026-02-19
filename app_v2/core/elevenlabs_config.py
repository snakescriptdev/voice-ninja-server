"""
ElevenLabs Configuration Module

This module contains all ElevenLabs API related configurations including:
- Default models and languages
- Valid LLM models supported by ElevenLabs
- ElevenLabs TTS models with their supported languages
- API endpoints and constants
"""

from typing import List, Dict, Any
from app_v2.core.config import VoiceSettings

# ============================================================================
# API Configuration
# ============================================================================

ELEVENLABS_API_KEY = VoiceSettings.ELEVENLABS_API_KEY
BASE_URL = "https://api.elevenlabs.io/v1"

# ============================================================================
# Default Configuration
# ============================================================================

DEFAULT_LLM_ELEVENLAB = "gemini-2.5-flash"  # Default LLM model
DEFAULT_MODEL_ELEVENLAB = "eleven_flash_v2_5"  # Default ElevenLabs TTS model (supports multilingual)

DEFAULT_LANGUAGE = "en"  # Default language code

# ============================================================================
# Valid LLM Models
# As per ElevenLabs documentation
# ============================================================================

VALID_LLMS: list[str] = [

    # =====================
    # GPT (OpenAI)
    # =====================
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-1106",
    "gpt-3.5-turbo-0125",

    
    "gpt-4-0314",
    "gpt-4-0613",

    "gpt-4-turbo",
    "gpt-4-turbo-2024-04-09",

    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4o-2024-05-13",
    "gpt-4o-2024-08-06",
    "gpt-4o-2024-11-20",
    "gpt-4o-mini-2024-07-18",

    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4.1-2025-04-14",
    "gpt-4.1-mini-2025-04-14",
    "gpt-4.1-nano-2025-04-14",

    "gpt-5",
    "gpt-5.1",
    "gpt-5.2",
    "gpt-5.2-chat-latest",

    "gpt-5-mini",
    "gpt-5-nano",

    "gpt-5-2025-08-07",
    "gpt-5.1-2025-11-13",
    "gpt-5.2-2025-12-11",
    "gpt-5-mini-2025-08-07",
    "gpt-5-nano-2025-08-07",

    # =====================
    # Gemini (Google)
    # =====================
    "gemini-1.5-pro",
    "gemini-1.5-pro-001",
    "gemini-1.5-pro-002",

    "gemini-1.5-flash",
    "gemini-1.5-flash-001",
    "gemini-1.5-flash-002",

    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",

    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",

    "gemini-2.5-flash-preview-04-17",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-flash-preview-09-2025",
    "gemini-2.5-flash-lite-preview-06-17",
    "gemini-2.5-flash-lite-preview-09-2025",

    "gemini-3-pro-preview",
    "gemini-3-flash-preview",

    # =====================
    # Claude (Anthropic)
    # =====================
    "claude-3-haiku",
    "claude-3-haiku@20240307",

    "claude-3-5-sonnet",
    "claude-3-5-sonnet-v1",
    "claude-3-5-sonnet-v2@20241022",
    "claude-3-5-sonnet@20240620",

    "claude-3-7-sonnet",
    "claude-3-7-sonnet@20250219",

    "claude-sonnet-4",
    "claude-sonnet-4@20250514",

    "claude-sonnet-4-5",
    "claude-sonnet-4-5@20250929",

    "claude-haiku-4-5",
    "claude-haiku-4-5@20251001",

    # =====================
    # Other / OSS / Infra
    # =====================
    "grok-beta",
    "custom-llm",

    "qwen3-4b",
    "qwen3-30b-a3b",

    "gpt-oss-20b",
    "gpt-oss-120b",

    "glm-45-air-fp8",

    "watt-tool-8b",
    "watt-tool-70b",
]


# ============================================================================
# ElevenLabs TTS Models with Supported Languages
# Reference: https://elevenlabs.io/docs/models
# ============================================================================

ELEVENLABS_MODELS: List[Dict[str, Any]] = [
    {
        "name": "eleven_turbo_v2_5",
        "description": "Latest turbo model with multilingual support",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "es", "name": "Spanish"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "it", "name": "Italian"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "hi", "name": "Hindi"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
            {"code": "ko", "name": "Korean"},
            {"code": "nl", "name": "Dutch"},
            {"code": "pl", "name": "Polish"},
            {"code": "sv", "name": "Swedish"},
            {"code": "da", "name": "Danish"},
            {"code": "fi", "name": "Finnish"},
            {"code": "no", "name": "Norwegian"},
        ],
    },
    {
        "name": "eleven_flash_v2_5",
        "description": "Fast and efficient model with multilingual support",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "es", "name": "Spanish"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "it", "name": "Italian"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "hi", "name": "Hindi"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
            {"code": "ko", "name": "Korean"},
            {"code": "nl", "name": "Dutch"},
            {"code": "pl", "name": "Polish"},
            {"code": "sv", "name": "Swedish"},
            {"code": "da", "name": "Danish"},
            {"code": "fi", "name": "Finnish"},
            {"code": "no", "name": "Norwegian"},
        ],
    },
    {
        "name": "eleven_multilingual_v2",
        "description": "Multilingual model with extensive language support",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
            {"code": "de", "name": "German"},
            {"code": "hi", "name": "Hindi"},
            {"code": "fr", "name": "French"},
            {"code": "ko", "name": "Korean"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "it", "name": "Italian"},
            {"code": "es", "name": "Spanish"},
            {"code": "id", "name": "Indonesian"},
            {"code": "nl", "name": "Dutch"},
            {"code": "tr", "name": "Turkish"},
            {"code": "fil", "name": "Filipino"},
            {"code": "pl", "name": "Polish"},
            {"code": "sv", "name": "Swedish"},
            {"code": "bg", "name": "Bulgarian"},
            {"code": "ro", "name": "Romanian"},
            {"code": "ar", "name": "Arabic"},
            {"code": "cs", "name": "Czech"},
            {"code": "el", "name": "Greek"},
            {"code": "fi", "name": "Finnish"},
            {"code": "hr", "name": "Croatian"},
            {"code": "ms", "name": "Malay"},
            {"code": "sk", "name": "Slovak"},
            {"code": "da", "name": "Danish"},
            {"code": "ta", "name": "Tamil"},
            {"code": "uk", "name": "Ukrainian"},
            {"code": "ru", "name": "Russian"},
        ],
    },
    {
        "name": "eleven_turbo_v2",
        "description": "Turbo model with limited language support",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "es", "name": "Spanish"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "it", "name": "Italian"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "hi", "name": "Hindi"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
        ],
    },
    {
        "name": "eleven_monolingual_v1",
        "description": "English-only model",
        "languages": [
            {"code": "en", "name": "English"},
        ],
    },
    {
        "name": "eleven_multilingual_stable_v1",
        "description": "Stable multilingual model",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "es", "name": "Spanish"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "it", "name": "Italian"},
            {"code": "pt", "name": "Portuguese"},
            {"code": "hi", "name": "Hindi"},
            {"code": "ja", "name": "Japanese"},
            {"code": "zh", "name": "Chinese"},
        ],
    },
]

# ============================================================================
# Helper Functions
# ============================================================================

def get_all_supported_languages() -> List[Dict[str, str]]:
    """
    Get all unique languages supported across all ElevenLabs models.
    
    Returns:
        List of unique language dictionaries with 'code' and 'name' keys
    """
    unique_languages = {}
    for model in ELEVENLABS_MODELS:
        for lang in model["languages"]:
            if lang["code"] not in unique_languages:
                unique_languages[lang["code"]] = lang
    
    return list(unique_languages.values())


def get_languages_for_model(model_name: str) -> List[Dict[str, str]]:
    """
    Get supported languages for a specific ElevenLabs model.
    
    Args:
        model_name: Name of the ElevenLabs model
        
    Returns:
        List of language dictionaries for the model, or empty list if model not found
    """
    for model in ELEVENLABS_MODELS:
        if model["name"] == model_name:
            return model["languages"]
    return []


def is_valid_llm(model_name: str) -> bool:
    """
    Check if a given LLM model name is valid/supported by ElevenLabs.
    
    Args:
        model_name: Name of the LLM model
        
    Returns:
        True if valid, False otherwise
    """
    return model_name in VALID_LLMS


def get_model_names() -> List[str]:
    """
    Get list of all ElevenLabs TTS model names.
    
    Returns:
        List of model names
    """
    return [model["name"] for model in ELEVENLABS_MODELS]


# ============================================================================
# Model Compatibility Helpers
# ============================================================================

ENGLISH_CODES = ["en", "en-US", "en-GB"]
EN_MODELS = ["eleven_turbo_v2", "eleven_flash_v2", "eleven_monolingual_v1"]
NON_EN_MODELS = ["eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_multilingual_stable_v1"]


def get_compatible_model_for_language(language_code: str, preferred_model: str = None) -> str:
    """
    Get a compatible ElevenLabs model for a given language.
    
    Args:
        language_code: Language code (e.g., 'en', 'es', 'fr')
        preferred_model: Preferred model if compatible, otherwise will auto-select
        
    Returns:
        Compatible model name
    """
    # If English, prefer EN_MODELS
    if language_code in ENGLISH_CODES:
        if preferred_model and preferred_model in EN_MODELS:
            return preferred_model
        return "eleven_turbo_v2"
    
    # For non-English, prefer NON_EN_MODELS
    if preferred_model and preferred_model in NON_EN_MODELS:
        # Check if the model supports this language
        languages = get_languages_for_model(preferred_model)
        if any(lang["code"] == language_code for lang in languages):
            return preferred_model
    
    # Default to eleven_turbo_v2_5 for multilingual
    return DEFAULT_MODEL_ELEVENLAB
