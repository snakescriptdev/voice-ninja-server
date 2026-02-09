"""
Populate ElevenLabs Data Script

This standalone script populates the database with:
1. Supported languages from ElevenLabs models
2. AI Models (LLMs) supported by ElevenLabs
3. Voices (both prebuilt and user-created from ElevenLabs)

Usage:
    python -m app_v2.scripts.populate_elevenlabs_data
"""

import sys
import logging
from sqlalchemy.orm import Session, sessionmaker
from app_v2.databases.models import (
    Base, 
    engine, 
    LanguageModel, 
    AIModels, 
    VoiceModel, 
    VoiceTraitsModel
)
from app_v2.core.elevenlabs_config import (
    get_all_supported_languages,
    VALID_LLMS,
    ELEVENLABS_API_KEY
)



# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create session
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def populate_languages(session: Session):
    """
    Populate the languages table with all supported languages from ElevenLabs models.
    """
    logger.info("=" * 60)
    logger.info("Populating Languages...")
    logger.info("=" * 60)
    
    languages = get_all_supported_languages()
    added_count = 0
    existing_count = 0
    
    for lang in languages:
        # Check if language already exists
        existing = session.query(LanguageModel).filter(
            LanguageModel.lang_code == lang["code"]
        ).first()
        
        if existing:
            logger.debug(f"Language '{lang['name']}' ({lang['code']}) already exists")
            existing_count += 1
            continue
        
        # Create new language
        new_language = LanguageModel(
            lang_code=lang["code"],
            language=lang["name"]
        )
        session.add(new_language)
        logger.info(f"✅ Added language: {lang['name']} ({lang['code']})")
        added_count += 1
    
    try:
        session.commit()
        logger.info(f"\n📊 Summary: {added_count} languages added, {existing_count} already existed")
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Error populating languages: {e}")
        raise


def populate_ai_models(session: Session):
    """
    Populate the AI models table with all valid LLMs from ElevenLabs.
    """
    logger.info("\n" + "=" * 60)
    logger.info("Populating AI Models (LLMs)...")
    logger.info("=" * 60)
    
    # Map models to their providers
    model_providers = {
        "gpt": "OpenAI",
        "gemini": "Google",
        "claude": "Anthropic",
        "grok": "xAI",
        "qwen": "Alibaba",
        "custom-llm": "Custom"
    }
    
    added_count = 0
    existing_count = 0
    
    for model_name in VALID_LLMS:
        # Check if model already exists
        existing = session.query(AIModels).filter(
            AIModels.model_name == model_name
        ).first()
        
        if existing:
            logger.debug(f"Model '{model_name}' already exists")
            existing_count += 1
            continue
        
        # Determine provider based on model name prefix
        provider = "Other"
        for prefix, prov in model_providers.items():
            if model_name.lower().startswith(prefix):
                provider = prov
                break
        
        # Create new AI model
        new_model = AIModels(
            provider=provider,
            model_name=model_name
        )
        session.add(new_model)
        logger.info(f"✅ Added AI model: {model_name} (Provider: {provider})")
        added_count += 1
    
    try:
        session.commit()
        logger.info(f"\n📊 Summary: {added_count} AI models added, {existing_count} already existed")
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Error populating AI models: {e}")
        raise


def populate_elevenlabs_voices(session: Session):
    logger.info("Populating ElevenLabs voices...")

    if not ELEVENLABS_API_KEY:
        logger.warning("ELEVENLABS_API_KEY not set. Skipping.")
        return

    import requests
    from app_v2.core.elevenlabs_config import BASE_URL

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }

    response = requests.get(f"{BASE_URL}/voices", headers=headers)

    if response.status_code != 200:
        logger.error(f"Failed to fetch voices: {response.text}")
        return

    voices = response.json().get("voices", [])

    for voice in voices:
        voice_id = voice.get("voice_id")
        voice_name = voice.get("name")

        if not voice_id or not voice_name:
            continue

        labels = voice.get("labels") or {}

        raw_gender = labels.get("gender")
        raw_accent = labels.get("accent")

        gender = raw_gender if raw_gender in ("male", "female") else None
        nationality = raw_accent  # can be None

        existing_voice = session.query(VoiceModel).filter(
            VoiceModel.elevenlabs_voice_id == voice_id
        ).first()

        if existing_voice:
            existing_voice.voice_name = voice_name

            traits = session.query(VoiceTraitsModel).filter(
                VoiceTraitsModel.voice_id == existing_voice.id
            ).first()

            if traits:
                traits.gender = gender
                traits.nationality = nationality
            continue

        new_voice = VoiceModel(
            voice_name=voice_name,
            elevenlabs_voice_id=voice_id,
            is_custom_voice=False,
            user_id=None,
        )
        session.add(new_voice)
        session.flush()

        traits = VoiceTraitsModel(
            voice_id=new_voice.id,
            gender=gender,
            nationality=nationality,
        )
        session.add(traits)

    session.commit()
    logger.info("ElevenLabs voice sync completed.")



def main():
    """
    Main function to populate all ElevenLabs data.
    """
    logger.info("\n" + "🚀" * 30)
    logger.info("ELEVENLABS DATA POPULATION SCRIPT")
    logger.info("🚀" * 30 + "\n")
    
    # Ensure tables exist
    logger.info("Ensuring database tables exist...")
    Base.metadata.create_all(engine)
    logger.info("✅ Database tables ready\n")
    
    session = SessionLocal()
    
    try:
        # Populate data in order
        populate_languages(session)
        populate_ai_models(session)
        populate_elevenlabs_voices(session)
        
        logger.info("\n" + "✨" * 30)
        logger.info("DATA POPULATION COMPLETE!")
        logger.info("✨" * 30 + "\n")
        
    except Exception as e:
        logger.error(f"\n❌ Fatal error during population: {e}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
