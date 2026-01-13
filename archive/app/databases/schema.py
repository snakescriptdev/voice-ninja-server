from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

class AudioRecordSchema(BaseModel):
    id: int
    file_name: Optional[str] = ""
    duration: Optional[float] = 0
    voice: Optional[str] = ""
    created_at: Optional[datetime] = None
    file_url: Optional[str] = ""
    email: Optional[str] = ""
    number: Optional[str] = ""

    class Config:
        from_attributes = True

class AudioRecordListSchema(BaseModel):
    audio_records: List[AudioRecordSchema]

    class Config:
        from_attributes = True

    def model_dump(self, mode="json", request=None):
        data = super().model_dump(mode=mode)
        if request:
            for d in data['audio_records']:
                # Generate full URL using request base URL and file path
                base_url = str(request.base_url).rstrip('/')
                d['file_url'] = f"{base_url}/audio/{d['file_name']}"
        return data
