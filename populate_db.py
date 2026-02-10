"""
Populate ElevenLabs Data Script (Unified)

This script:
1. Ensures DB tables exist
2. Populates:
   - Supported languages
   - AI models (LLMs)
   - ElevenLabs voices
3. Removes hardcoded default voices
4. Ensures admin default rows exist

Usage:
    python -m app_v2.scripts.populate_elevenlabs_data
"""

import sys
import logging
import requests
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
)

from app_v2.core.elevenlabs_config import (
    get_all_supported_languages,
    VALID_LLMS,
    ELEVENLABS_API_KEY,
    BASE_URL,
)

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

DEFAULT_VOICES = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]

# ------------------------------------------------------------------
# CLEANUP
# ------------------------------------------------------------------

def remove_default_voices(session: Session):
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

# ------------------------------------------------------------------
# POPULATORS
# ------------------------------------------------------------------

def populate_languages(session: Session):
    logger.info("Populating languages...")

    languages = get_all_supported_languages()
    for lang in languages:
        exists = session.query(LanguageModel).filter(
            LanguageModel.lang_code == lang["code"]
        ).first()
        if exists:
            continue

        session.add(
            LanguageModel(
                lang_code=lang["code"],
                language=lang["name"],
            )
        )

    session.commit()
    logger.info("✅ Languages populated")


def populate_ai_models(session: Session):
    logger.info("Populating AI models...")

    providers = {
        "gpt": "OpenAI",
        "gemini": "Google",
        "claude": "Anthropic",
        "grok": "xAI",
        "qwen": "Alibaba",
        "custom": "Custom",
    }

    for model in VALID_LLMS:
        exists = session.query(AIModels).filter(
            AIModels.model_name == model
        ).first()
        if exists:
            continue

        provider = "Other"
        for prefix, name in providers.items():
            if model.lower().startswith(prefix):
                provider = name
                break

        session.add(AIModels(model_name=model, provider=provider))

    session.commit()
    logger.info("✅ AI models populated")


def populate_elevenlabs_voices(session: Session):
    logger.info("Syncing ElevenLabs voices...")

    if not ELEVENLABS_API_KEY:
        logger.warning("ELEVENLABS_API_KEY not set. Skipping voice sync.")
        return

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }

    response = requests.get(f"{BASE_URL}/voices", headers=headers)
    if response.status_code != 200:
        logger.error(f"Failed to fetch voices: {response.text}")
        return

    for voice in response.json().get("voices", []):
        voice_id = voice.get("voice_id")
        voice_name = voice.get("name")
        labels = voice.get("labels") or {}

        if not voice_id or not voice_name:
            continue

        gender = labels.get("gender") if labels.get("gender") in ("male", "female") else None
        nationality = labels.get("accent")

        existing = session.query(VoiceModel).filter(
            VoiceModel.elevenlabs_voice_id == voice_id
        ).first()

        if existing:
            existing.voice_name = voice_name
            traits = session.query(VoiceTraitsModel).filter(
                VoiceTraitsModel.voice_id == existing.id
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

        session.add(
            VoiceTraitsModel(
                voice_id=new_voice.id,
                gender=gender,
                nationality=nationality,
            )
        )

    session.commit()
    logger.info("✅ ElevenLabs voices synced")

# ------------------------------------------------------------------
# DEFAULT ADMIN DATA
# ------------------------------------------------------------------

def ensure_admin_defaults(session: Session):
    logger.info("Ensuring admin defaults...")

    if not session.query(AdminTokenModel).filter_by(id=1).first():
        session.add(AdminTokenModel(id=1, token_values=0, free_tokens=0))

    if not session.query(TokensToConsume).filter_by(id=1).first():
        session.add(TokensToConsume(id=1, token_values=0))

    session.commit()
    logger.info("✅ Admin defaults ready")

# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------

def main():
    logger.info("🚀 Starting ElevenLabs DB bootstrap")

    Base.metadata.create_all(engine)
    session = SessionLocal()

    try:
        ensure_admin_defaults(session)
        remove_default_voices(session)
        populate_languages(session)
        populate_ai_models(session)
        populate_elevenlabs_voices(session)

        logger.info("✨ All data successfully populated")

    except Exception as e:
        session.rollback()
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)

    finally:
        session.close()


if __name__ == "__main__":
    main()
