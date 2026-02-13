from pydantic import BaseModel, Field
from fastapi import UploadFile
from typing import Optional
from app_v2.schemas.enum_types import GenderEnum



class VoiceUpdate(BaseModel):
    voice_name: Optional[str] = None
    gender: Optional[GenderEnum]= None
    nationality: Optional[str] = None

class VoiceRead(BaseModel):
    id: int
    voice_name:str
    is_custom_voice: bool
    elevenlabs_voice_id: Optional[str] | None
    gender: Optional[GenderEnum] = GenderEnum.male
    nationality: Optional[str] = None
    has_sample_audio: Optional[bool] = False
    sample_audio_url: Optional[str] = None
    

    class Config:
        from_attributes = True

    