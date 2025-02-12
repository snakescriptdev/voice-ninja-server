from pydantic import BaseModel
from datetime import datetime
from typing import List

class AudioRecordSchema(BaseModel):
    id: int
    file_name: str
    duration: float
    voice: str
    created_at: datetime
    file_url: str = ""

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
