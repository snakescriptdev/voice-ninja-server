from pydantic import BaseModel, Field
from fastapi import UploadFile
from typing import Optional


class BaseVoiceSchema(BaseModel):
    voice_name: str
    is_custom_voice: bool
    


class VoiceIn(BaseVoiceSchema):
    user_id: Optional[int] = None
    elevenlabs_voice_id: Optional[int] = None
    audio_file: Optional[UploadFile] = None


class VoiceUpdate(BaseModel):
    voice_name: Optional[str] = None
    is_custom_voice: Optional[bool] = None
    user_id: Optional[int] = None
    elevenlabs_voice_id: Optional[int] = None
    audio_file: Optional[UploadFile] = None



class VoiceRead(BaseVoiceSchema):
    id: int
    elevenlabs_voice_id: Optional[str] | None

    