from pydantic import BaseModel, HttpUrl
from typing import Optional
from datetime import datetime

class KnowledgeBaseURLCreate(BaseModel):
    url: HttpUrl

class KnowledgeBaseTextCreate(BaseModel):
    title: str
    context: str

class KnowledgeBaseFileUpdate(BaseModel):
    title: Optional[str] = None

class KnowledgeBaseURLUpdate(BaseModel):
    title: Optional[str] = None
    url: Optional[HttpUrl] = None

class KnowledgeBaseTextUpdate(BaseModel):
    title: Optional[str] = None
    content_text: Optional[str] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    kb_type: str
    title: Optional[str] = None
    content_path: Optional[str] = None
    content_text: Optional[str] = None
    elevenlabs_document_id: Optional[str] = None
    file_size: Optional[float] = None
    created_at: datetime
    modified_at: datetime

    class Config:
        from_attributes = True

class KnowledgeBaseBind(BaseModel):
    agent_id: int
    kb_id: int
