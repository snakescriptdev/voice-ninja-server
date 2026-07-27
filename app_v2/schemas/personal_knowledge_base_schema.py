from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional
from datetime import datetime


class PersonalKnowledgeBaseURLCreate(BaseModel):
    url: HttpUrl


class PersonalKnowledgeBaseTextCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=300)
    content: str = Field(..., min_length=2, max_length=300)


class PersonalKnowledgeBaseURLUpdate(BaseModel):
    title: Optional[str] = None
    url: Optional[HttpUrl] = None


class PersonalKnowledgeBaseTextUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=300)
    content: Optional[str] = Field(default=None, min_length=2, max_length=300)


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
    # LLM-authored summary of recent turns relevant to `query` — the agent's
    # own model fills this in based on its conversation context, same as it
    # fills `query`. Optional since there may be no prior context yet (e.g.
    # the very first turn), or the model may omit it.
    conversation_context: Optional[str] = None


class PersonalKnowledgeBaseAnswerResponse(BaseModel):
    """Response returned to the search_personal_knowledge_base tool call —
    a ready-to-speak answer synthesized from the retrieved KB excerpts, plus
    the raw excerpts themselves for transparency/debugging."""
    answer: str
