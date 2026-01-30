from pydantic import BaseModel, Field
from fastapi import UploadFile
from typing import Optional


class VoiceRead(BaseModel):
    id: int
    voice_name:str
    is_custom_voice: bool
    elevenlabs_voice_id: Optional[str] | None

    class Config:
        orm_mode = True

    