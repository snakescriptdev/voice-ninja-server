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
    VoiceTraitsModel,
    AdminTokenModel,
    TokensToConsume,
    AgentModel
)
import requests
from app_v2.core.elevenlabs_config import (
    get_all_supported_languages,
    VALID_LLMS,
    ELEVENLABS_API_KEY,
    BASE_URL
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
    
    # 2) Identify voices to remove
    # We want to remove any prebuilt/shared voice that is NOT in the user's personal voice list
    personal_voice_ids = {v.get("voice_id") for v in voices if v.get("voice_id")}
    
    # Get all prebuilt voices from DB (is_custom_voice=False, user_id=None)
    # OR any voice that has an elevenlabs_voice_id but is NOT in our personal list
    db_voices = session.query(VoiceModel).all()
    
    removed_count = 0
    for db_voice in db_voices:
        # Rules for removal:
        # 1. If it has an ElevenLabs ID but that ID is NOT in the personal list, we consider it "orphan" or "shared" and remove it
        # 2. We only do this for voices that are NOT custom voices owned by users (those should be preserved)
        if db_voice.elevenlabs_voice_id and db_voice.elevenlabs_voice_id not in personal_voice_ids:
            if not db_voice.is_custom_voice or db_voice.user_id is None:
                logger.info(f"🗑️ Removing orphan/shared voice: {db_voice.voice_name} ({db_voice.elevenlabs_voice_id})")
                
                # Cleanup agents using this voice
                agents = session.query(AgentModel).filter(AgentModel.agent_voice == db_voice.id).all()
                for agent in agents:
                    logger.info(f"  - Deleting agent '{agent.agent_name}' (ID: {agent.id}) linked to removed voice")
                    session.delete(agent)
                
                # Cleanup traits
                session.query(VoiceTraitsModel).filter(VoiceTraitsModel.voice_id == db_voice.id).delete()
                
                # Delete the voice
                session.delete(db_voice)
                removed_count += 1

    if removed_count > 0:
        session.commit()
        logger.info(f"✅ Cleanup complete: Removed {removed_count} orphan/shared voices and their agents.")

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
        preview_url = voice.get("preview_url")

        raw_gender = labels.get("gender")
        raw_accent = labels.get("accent")

        gender = raw_gender if raw_gender in ("male", "female") else None
        nationality = raw_accent

        has_sample_audio = bool(preview_url)

        # 1️⃣ Try match by ElevenLabs ID
        existing_voice = session.query(VoiceModel).filter(
            VoiceModel.elevenlabs_voice_id == voice_id
        ).first()

        # 2️⃣ If not found, try match by name
        if not existing_voice:
            existing_voice = session.query(VoiceModel).filter(
                func.lower(VoiceModel.voice_name) == voice_name.lower(),
                VoiceModel.is_custom_voice.is_(False),
                VoiceModel.user_id.is_(None),
            ).first()

        # ✅ UPDATE EXISTING
        if existing_voice:
            existing_voice.voice_name = voice_name
            existing_voice.elevenlabs_voice_id = voice_id
            existing_voice.has_sample_audio = has_sample_audio
            existing_voice.audio_file = preview_url if has_sample_audio else None

            traits = session.query(VoiceTraitsModel).filter(
                VoiceTraitsModel.voice_id == existing_voice.id
            ).first()

            if traits:
                traits.gender = gender
                traits.nationality = nationality

            continue

        # ✅ CREATE NEW VOICE
        new_voice = VoiceModel(
            voice_name=voice_name,
            elevenlabs_voice_id=voice_id,
            is_custom_voice=False,
            user_id=None,
            has_sample_audio=has_sample_audio,
            audio_file=preview_url if has_sample_audio else None,
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
    logger.info("Removing hardcoded default voices and related agents...")

    DEFAULT_VOICES = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]

    voices = (
        session.query(VoiceModel)
        .filter(
            VoiceModel.voice_name.in_(DEFAULT_VOICES),
            VoiceModel.is_custom_voice.is_(False),
        )
        .all()
    )

    

    for voice in voices:
        logger.info(f"Processing voice: {voice.voice_name}")

        # 1️⃣ Delete agents using this voice
        agents = (
            session.query(AgentModel)
            .filter(AgentModel.agent_voice == voice.id)
            .all()
        )
        if agents:
            for agent in agents:
                logger.info(f"Deleting agent '{agent.id}' linked to voice '{voice.voice_name}'")
                session.delete(agent)
                

        session.query(VoiceTraitsModel).filter(
                VoiceTraitsModel.voice_id == voice.id
            ).delete(synchronize_session=False)

        # 3️⃣ Delete voice
        logger.info(f"Deleting voice: {voice.voice_name}")
        session.delete(voice)


    session.commit()

    logger.info("✅ Default voices cleanup complete.")


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
        ensure_admin_defaults(session)
        populate_languages(session)
        populate_ai_models(session)
        remove_default_voices_unsynced(session)
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
