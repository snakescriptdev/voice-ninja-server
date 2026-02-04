from pydantic import BaseModel, HttpUrl
from typing import Optional
from datetime import datetime

class KnowledgeBaseURLCreate(BaseModel):
    agent_name: str
    url: HttpUrl

class KnowledgeBaseTextCreate(BaseModel):
    agent_name: str
    title: str
    context: str

class KnowledgeBaseUpdate(BaseModel):
    title: Optional[str] = None
    content_text: Optional[str] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    agent_id: int
    kb_type: str
    title: Optional[str] = None
    content_path: Optional[str] = None
    content_text: Optional[str] = None
    created_at: datetime
    modified_at: datetime

    class Config:
        from_attributes = True
