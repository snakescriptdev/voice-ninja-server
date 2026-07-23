from pydantic import BaseModel, HttpUrl, field_serializer
from typing import Optional
from datetime import datetime

class KnowledgeBaseURLCreate(BaseModel):
    url: HttpUrl

class KnowledgeBaseTextCreate(BaseModel):
    title: str
    content: str

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
    num_pages: Optional[int] = None
    created_at: datetime
    modified_at: datetime
    # Number of the current user's agents this KB item is bound to.
    agents_count: int = 0

    @field_serializer('created_at', 'modified_at')
    def serialize_datetime(self, dt: datetime):
        return dt.date()

    class Config:
        from_attributes = True

class KnowledgeBaseBind(BaseModel):
    agent_id: int
    kb_id: int

class KnowledgeBaseAgentItem(BaseModel):
    id: int
    agent_name: str
