from pydantic import BaseModel
from typing import List, Optional

class ErrorResponse(BaseModel):
    error: str

class SuccessResponse(BaseModel):
    message: str

class AudioFileResponse(BaseModel):
    filename: str
    session_id: str
    file_url: Optional[str] = None

class AudioFileListResponse(BaseModel):
    audio_files: List[AudioFileResponse] 