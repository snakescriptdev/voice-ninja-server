"""
This file has CRUD routes defined for the voice 
"""
from fastapi import HTTPException, APIRouter,status, Depends, Form, UploadFile
from app_v2.utils.jwt_utils import HTTPBearer, get_current_user
from fastapi_sqlalchemy import db
from typing import Optional, List
from app_v2.schemas.voice_schema import  VoiceRead
from sqlalchemy import or_, and_
from app_v2.databases.models import VoiceModel, UnifiedAuthModel
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

security = HTTPBearer()

router = APIRouter(prefix="/api/v2",tags=["agent"],dependencies=[Depends(security)])






@router.get("/voice",response_model=List[VoiceRead],status_code=status.HTTP_200_OK,openapi_extra={"security":[{"BearerAuth":[]}]},summary="lists available voices",description="return the list of available voices for user (both custom and predefined)")
async def get_all_voices(current_user:UnifiedAuthModel = Depends(get_current_user)):
    try:
        voices = db.session.query(VoiceModel).filter(or_(
            VoiceModel.user_id == current_user.id,
            VoiceModel.user_id.is_(None)
        )).all()

        if not voices:
            logger.info("no voices are present in database.")
            return []
        logger.info("voices fetched successfully from db")
        return voices
    except Exception as e:
        logger.error(f"error while fetching the voices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to load voices at the moment"
        )


@router.get("/voice/by-id/{id}",response_model=VoiceRead,status_code=status.HTTP_200_OK,openapi_extra={"security":[{"BearerAuth":[]}]})
async def get_voice_by_id(id:int, current_user:UnifiedAuthModel = Depends(get_current_user)):
    try:
        voice = db.session.query(VoiceModel).filter(
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
        return voice
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while fetching the voice: {e}")
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to fetch the voice at the moment"
        )

    





# @router.post("/",response_model=VoiceRead,openapi_extra={"security": [{"BearerAuth": []}]})
# async def create_agent(
#     voice_name: str = Form(...,description="name of the voice",min_length=3),
#     is_custom_voice: bool = Form(default=True,
#                                  description="tells if the voice is created by user or a predefiend voice"),
#     audio_file: UploadFile | None = UploadFile(None),
#     current_user:UnifiedAuthModel = Depends(get_current_user)
    
#     ):
#     pass
