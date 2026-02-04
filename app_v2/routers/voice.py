"""
This file has CRUD routes defined for the voice 
"""
from fastapi import HTTPException, APIRouter, status, Depends, Form, UploadFile, File
from app_v2.utils.jwt_utils import HTTPBearer, get_current_user
from fastapi_sqlalchemy import db
from typing import Optional, List
from app_v2.schemas.voice_schema import VoiceRead, VoiceUpdate
from app_v2.schemas.enum_types import GenderEnum
import os
import shutil
from datetime import datetime
from sqlalchemy import or_, and_
from dataclasses import dataclass
from sqlalchemy.orm import selectinload

from app_v2.databases.models import VoiceModel, UnifiedAuthModel, VoiceTraitsModel
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

security = HTTPBearer()

router = APIRouter(prefix="/api/v2", tags=["agent"], dependencies=[Depends(security)])

UPLOAD_DIR = "uploads/voices"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a"}

# -------------------- RESPONSE MAPPER --------------------

def voice_to_read(voice: VoiceModel) -> VoiceRead:
    gender = "Male"
    nationality = "British"
    
    if voice.traits:
        gender = voice.traits.gender.value if hasattr(voice.traits.gender, 'value') else str(voice.traits.gender)
        nationality = voice.traits.nationality

    return VoiceRead(
        id=voice.id,
        voice_name=voice.voice_name,
        is_custom_voice=voice.is_custom_voice,
        elevenlabs_voice_id=voice.elevenlabs_voice_id,
        gender=gender,
        nationality=nationality
    )

@router.get("/voice", response_model=List[VoiceRead], status_code=status.HTTP_200_OK, openapi_extra={"security":[{"BearerAuth":[]}]}, summary="lists available voices", description="return the list of available voices for user (both custom and predefined)")
async def get_all_voices(current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        voices = db.session.query(VoiceModel).options(selectinload(VoiceModel.traits)).filter(or_(
            VoiceModel.user_id == current_user.id,
            VoiceModel.user_id.is_(None)
        )).all()

        if not voices:
            logger.info("no voices are present in database.")
            return []
        
        logger.info("voices fetched successfully from db")
        return [voice_to_read(voice) for voice in voices]
    except Exception as e:
        logger.error(f"error while fetching the voices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to load voices at the moment"
        )


@router.get("/voice/by-id/{id}", response_model=VoiceRead, status_code=status.HTTP_200_OK, openapi_extra={"security":[{"BearerAuth":[]}]})
async def get_voice_by_id(id: int, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        voice = db.session.query(VoiceModel).options(selectinload(VoiceModel.traits)).filter(
            and_(
                VoiceModel.id == id,
                or_(
                    VoiceModel.user_id == current_user.id,
                    VoiceModel.user_id.is_(None)
                )
            )
        ).first()

        if voice is None:
            logger.info("required voices are not present")
            raise HTTPException(
                status_code = status.HTTP_404_NOT_FOUND,
                detail= f"voice with the id: {id} not found"
            )
        logger.info("voice fetched successfully")
        return voice_to_read(voice)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while fetching the voice: {e}")
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to fetch the voice at the moment"
        )

@router.post("/voice", response_model=VoiceRead, status_code=status.HTTP_201_CREATED, openapi_extra={"security": [{"BearerAuth": []}]})
async def create_voice(
    voice_name: str = Form(..., description="name of the voice", min_length=3),
    gender: Optional[str] = Form(GenderEnum.male, description="gender of the voice (Male/Female)"),
    nationality: Optional[str] = Form("British", description="nationality of the voice"),
    file: UploadFile = File(...),
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        # Validate file extension
        _, ext = os.path.splitext(file.filename)
        if ext.lower() not in ALLOWED_AUDIO_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Invalid file type. Allowed: .mp3, .wav, .m4a")

        # Validate file size
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
             raise HTTPException(status_code=400, detail="File size exceeds 10MB limit")

        file_path = os.path.join(UPLOAD_DIR, f"{current_user.id}_{datetime.now().timestamp()}_{file.filename}")
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        with db():
            # Check if voice name exists for user
            existing_voice = db.session.query(VoiceModel).filter(
                VoiceModel.voice_name == voice_name,
                VoiceModel.user_id == current_user.id
            ).first()
            if existing_voice:
                 raise HTTPException(status_code=400, detail="Voice with this name already exists")
            gender = gender.lower()
            if gender not in [GenderEnum.male, GenderEnum.female]:
                raise HTTPException(status_code=400, detail="Invalid gender. Must be 'male' or 'female'")
            voice = VoiceModel(
                voice_name=voice_name,
                is_custom_voice=True,
                user_id=current_user.id,
                audio_file=file_path
            )
            db.session.add(voice)
            db.session.flush()
            
            # Create traits with provided or default values
            # Using simple string for now, could be validated against Enum if needed, but model handles Enum for gender
            # Ideally should map string to Enum, but let's see if plain string works or if we need validation
            
            traits = VoiceTraitsModel(
                voice_id=voice.id,
                gender=gender,
                nationality=nationality
            )
            db.session.add(traits)
            
            db.session.commit()
            db.session.refresh(voice)
            
            logger.info(f"Custom voice created: {voice_name}")
            return voice_to_read(voice)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error creating voice: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/voice/{voice_id}", status_code=status.HTTP_204_NO_CONTENT, openapi_extra={"security": [{"BearerAuth": []}]})
async def delete_voice(
    voice_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            voice = db.session.query(VoiceModel).filter(
                VoiceModel.id == voice_id,
                VoiceModel.user_id == current_user.id
            ).first()
            
            if not voice:
                raise HTTPException(status_code=404, detail="Voice not found")
            
            # Delete file if exists
            if voice.audio_file and os.path.exists(voice.audio_file):
                try:
                    os.remove(voice.audio_file)
                except OSError as e:
                    logger.warning(f"Failed to delete voice file {voice.audio_file}: {e}")

            db.session.delete(voice)
            db.session.commit()
            
            logger.info(f"Deleted voice {voice_id}")
            return
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error deleting voice: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/voice/{voice_id}", response_model=VoiceRead, openapi_extra={"security": [{"BearerAuth": []}]})
async def update_voice(
    voice_id: int,
    voice_update: VoiceUpdate,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            voice = db.session.query(VoiceModel).options(selectinload(VoiceModel.traits)).filter(
                VoiceModel.id == voice_id,
                VoiceModel.user_id == current_user.id
            ).first()
            
            if not voice:
                raise HTTPException(status_code=404, detail="Voice not found")
            
            if voice_update.voice_name:
                voice.voice_name = voice_update.voice_name
            
            # Handle traits update
            if voice_update.gender or voice_update.nationality:
                if not voice.traits:
                    # Create traits if not exists (defensive)
                    voice.traits = VoiceTraitsModel(
                        voice_id=voice.id,
                        gender=voice_update.gender or "Male", # fallback default
                        nationality=voice_update.nationality or "British"
                    )
                    db.session.add(voice.traits)
                else:
                    if voice_update.gender:
                         voice.traits.gender = voice_update.gender
                    if voice_update.nationality:
                         voice.traits.nationality = voice_update.nationality

            db.session.commit()
            # Explicitly refresh traits to ensure updated values are loaded
            db.session.refresh(voice)
            if voice.traits:
                db.session.refresh(voice.traits)
            
            logger.info(f"Updated voice {voice_id}")
            return voice_to_read(voice)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating voice: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
