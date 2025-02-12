from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ErrorResponse(BaseModel):
    error: str

class SuccessResponse(BaseModel):
    message: str

class AudioFileResponse(BaseModel):
    filename: str
    session_id: str
    file_url: Optional[str] = None
    created_at: Optional[datetime] = None
    voice: Optional[str] = None
    duration: Optional[float] = None

class AudioFileListResponse(BaseModel):
    audio_files: List[AudioFileResponse] 