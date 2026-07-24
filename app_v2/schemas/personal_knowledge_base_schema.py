from pydantic import BaseModel, HttpUrl, field_serializer, Field
from typing import List, Optional
from datetime import datetime


class PersonalKnowledgeBaseURLCreate(BaseModel):
    url: HttpUrl


class PersonalKnowledgeBaseTextCreate(BaseModel):
    title: str
    content: str


class PersonalKnowledgeBaseURLUpdate(BaseModel):
    title: Optional[str] = None
    url: Optional[HttpUrl] = None


class PersonalKnowledgeBaseTextUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


class PersonalKnowledgeBaseResponse(BaseModel):
    id: int
    kb_type: str
    title: Optional[str] = None
    content_path: Optional[str] = None
    content_text: Optional[str] = None
    file_size: Optional[float] = None
    num_chunks: int = 0
    created_at: datetime
    modified_at: datetime

    @field_serializer('created_at', 'modified_at')
    def serialize_datetime(self, dt: datetime):
        return dt.date()

    class Config:
        from_attributes = True


class PersonalKnowledgeBaseQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class PersonalKnowledgeBaseQueryResult(BaseModel):
    kb_id: int
    title: Optional[str] = None
    content: str
    score: float


class PersonalKnowledgeBaseQueryResponse(BaseModel):
    results: List[PersonalKnowledgeBaseQueryResult]


class ToolSearchRequest(BaseModel):
    """Body ElevenLabs sends when the agent invokes the personal KB tool."""
    query: str = Field(..., min_length=1)
