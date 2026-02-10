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
from sqlalchemy import func
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


def _fetch_shared_voices(headers: dict) -> list:
    """Fetch all shared (library) voices from GET /v1/shared-voices with pagination."""
    import requests
    url = "https://api.elevenlabs.io/v1/shared-voices"
    all_voices = []
    page = 0
    page_size = 100
    while True:
        resp = requests.get(url, headers=headers, params={"page_size": page_size, "page": page})
        if resp.status_code != 200:
            logger.warning("Failed to fetch shared voices (page=%s): %s", page, resp.text[:200])
            break
        data = resp.json()
        chunk = data.get("voices") or []
        # Normalize to same shape as /v1/voices: voice_id, name, labels { gender, accent }
        for v in chunk:
            all_voices.append({
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "labels": {
                    "gender": v.get("gender"),
                    "accent": v.get("accent"),
                },
            })
        if not data.get("has_more", False):
            break
        page += 1
    logger.info("Fetched %s shared (library) voices from ElevenLabs.", len(all_voices))
    return all_voices


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

    # 1) User's own voices (GET /v1/voices)
    response = requests.get(f"{BASE_URL}/voices", headers=headers)
    if response.status_code != 200:
        logger.error(f"Failed to fetch user voices: {response.text}")
        return

    voices = response.json().get("voices", [])

    # 2) Shared / library voices (GET /v1/shared-voices) so premade names like Puck, Kore sync
    shared = _fetch_shared_voices(headers)
    voices = voices + shared

    api_names = {v.get("name", "").strip().lower() for v in voices if v.get("name")}
    updated_by_id = 0
    updated_by_name = 0
    created = 0

    for voice in voices:
        voice_id = voice.get("voice_id")
        voice_name = (voice.get("name") or "").strip()

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
            updated_by_id += 1
        else:
            existing_voice = session.query(VoiceModel).filter(
                func.lower(VoiceModel.voice_name) == voice_name.lower(),
                VoiceModel.is_custom_voice.is_(False),
                VoiceModel.user_id.is_(None),
            ).first()
            if existing_voice:
                updated_by_name += 1

        if existing_voice:
            existing_voice.voice_name = voice_name
            existing_voice.elevenlabs_voice_id = voice_id

            traits = session.query(VoiceTraitsModel).filter(
                VoiceTraitsModel.voice_id == existing_voice.id
            ).first()

            if traits:
                traits.gender = gender
                traits.nationality = nationality
            continue

        created += 1
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
    logger.info(
        "ElevenLabs voice sync completed. updated_by_id=%s, updated_by_name=%s, created=%s",
        updated_by_id, updated_by_name, created,
    )
    # Warn if DB has prebuilt voices that weren't in the API (e.g. name mismatch or not in plan)
    prebuilt_without_el = session.query(VoiceModel).filter(
        VoiceModel.is_custom_voice.is_(False),
        VoiceModel.user_id.is_(None),
        VoiceModel.elevenlabs_voice_id.is_(None),
    ).all()
    if prebuilt_without_el:
        names = [v.voice_name for v in prebuilt_without_el]
        logger.warning(
            "These prebuilt voices have no ElevenLabs ID (not in API response or name mismatch): %s. "
            "API names (lowercase): %s",
            names, sorted(api_names),
        )

def remove_default_voices_unsynced(session: Session):
    logger.info("Removing hardcoded default voices...")

    voices = (
        session.query(VoiceModel)
        .filter(
            VoiceModel.voice_name.in_(DEFAULT_VOICES),
            VoiceModel.is_custom_voice == False,
        )
        .all()
    )

    for voice in voices:
        logger.info(f"Deleting traits for voice: {voice.voice_name}")
        session.query(VoiceTraitsModel).filter(
            VoiceTraitsModel.voice_id == voice.id
        ).delete(synchronize_session=False)

        logger.info(f"Deleting voice: {voice.voice_name}")
        session.delete(voice)

    session.commit()
    logger.info("✅ Default voices removed")

def ensure_admin_defaults(session: Session):
    logger.info("Ensuring admin defaults...")

    if not session.query(AdminTokenModel).filter_by(id=1).first():
        session.add(AdminTokenModel(id=1, token_values=0, free_tokens=0))

    if not session.query(TokensToConsume).filter_by(id=1).first():
        session.add(TokensToConsume(id=1, token_values=0))

    session.commit()
    logger.info("✅ Admin defaults ready")



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
        remove_default_voices_unsynced(session)
        ensure_admin_defaults(session)
        
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
