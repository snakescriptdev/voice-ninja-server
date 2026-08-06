from pydantic import BaseModel, Field
from fastapi import UploadFile
from typing import Optional
from app_v2.schemas.enum_types import GenderEnum



class VoiceUpdate(BaseModel):
    voice_name: Optional[str] = None
    gender: Optional[GenderEnum]= None
    nationality: Optional[str] = None
    is_enabled: Optional[bool] = None

class VoiceRead(BaseModel):
    id: int
    voice_name:str
    is_custom_voice: bool
    elevenlabs_voice_id: Optional[str] | None
    gender: Optional[GenderEnum] = GenderEnum.male
    nationality: Optional[str] = None
    has_sample_audio: Optional[bool] = False
    sample_audio_url: Optional[str] = None
    is_enabled: bool
    agents: list
    

    class Config:
        from_attributes = True


# -------------------------------------------------------------------
# Public API (app_v2/routers/public_api.py) only. Kept separate from
# VoiceRead above (shared with the internal voice.py router) so the
# public surface can omit internal-only fields (elevenlabs_voice_id,
# agents) without touching the internal schema.
# -------------------------------------------------------------------

class PublicVoiceListRead(BaseModel):
    id: int
    voice_name: str
    is_custom_voice: bool
    gender: Optional[GenderEnum] = GenderEnum.male
    nationality: Optional[str] = None
    has_sample_audio: Optional[bool] = False
    sample_audio_url: Optional[str] = None
    is_enabled: bool

    class Config:
        from_attributes = True


class PublicVoiceRead(BaseModel):
    id: int
    voice_name: str
    is_custom_voice: bool
    gender: Optional[GenderEnum] = GenderEnum.male
    nationality: Optional[str] = None
    has_sample_audio: Optional[bool] = False
    sample_audio_url: Optional[str] = None
    is_enabled: bool

    class Config:
        from_attributes = True
