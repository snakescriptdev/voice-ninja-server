from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class APIKeyCreate(BaseModel):
    name: Optional[str] = None

class APIKeyResponse(BaseModel):
    id: int
    name: Optional[str]
    client_id: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class APIKeyFullResponse(APIKeyResponse):
    client_secret: str # Only returned once upon creation
