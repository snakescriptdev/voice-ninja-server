from datetime import datetime
from dataclasses import dataclass

@dataclass
class AudioFileMetaData:
    SID:str
    voice:str
    created_at:datetime
    audio_type:str

@dataclass
class AudioFile:
    name: str
    url: str
    session_id: str
    voice: str
    created_at: datetime
    duration: float
    