import ipaddress
from pydantic import BaseModel, HttpUrl, Field, field_validator
from typing import List, Optional
from datetime import datetime


def _reject_local_or_private_url(v: HttpUrl) -> HttpUrl:
    """Blocks URLs pointing at localhost/loopback/private/link-local network
    addresses (e.g. http://localhost:3000, http://127.0.0.1, http://10.0.0.5)
    - these can never be reachable by our scraper from a different host and
    would otherwise be silently accepted and stored."""
    host = (v.host or "").lower()
    if not host:
        return v
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("URLs pointing to localhost are not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    ):
        raise ValueError("URLs pointing to private or internal network addresses are not allowed.")
    return v


def _non_blank(v: Optional[str]) -> Optional[str]:
    # Phrased to read naturally once get_readable_message prefixes it with
    # the field's display name (e.g. "Title cannot be empty or contain only
    # whitespace.") instead of as its own standalone sentence.
    if v is not None and not v.strip():
        raise ValueError("cannot be empty or contain only whitespace.")
    return v.strip() if v is not None else v


class PersonalKnowledgeBaseURLCreate(BaseModel):
    url: HttpUrl

    model_config = {"extra": "forbid"}

    _validate_url = field_validator("url")(_reject_local_or_private_url)


class PersonalKnowledgeBaseTextCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=300)
    content: str = Field(..., min_length=2, max_length=300)

    model_config = {"extra": "forbid"}

    _validate_title = field_validator("title")(_non_blank)
    _validate_content = field_validator("content")(_non_blank)


class PersonalKnowledgeBaseURLUpdate(BaseModel):
    """Partial-update (PATCH-style) shape used by the internal dashboard PUT
    - both fields optional, either can be sent alone."""
    title: Optional[str] = None
    url: Optional[HttpUrl] = None


class PersonalKnowledgeBaseTextUpdate(BaseModel):
    """Partial-update (PATCH-style) shape used by the internal dashboard PUT
    - both fields optional, either can be sent alone."""
    title: Optional[str] = Field(default=None, min_length=2, max_length=300)
    content: Optional[str] = Field(default=None, min_length=2, max_length=300)


class PersonalKnowledgeBaseURLPublicUpdate(BaseModel):
    """Full-replace payload for PUT /api/v2/public/personal-kb/{id}/url.

    Unlike PersonalKnowledgeBaseURLUpdate (internal, PATCH-style), `url` is
    mandatory - an empty body or a body missing `url` is rejected instead of
    silently keeping the existing value. `title` stays optional: when
    omitted/blank it falls back to the freshly-scraped page title, same as
    on creation (see update_personal_kb_url_public).
    """
    url: HttpUrl
    title: Optional[str] = None

    model_config = {"extra": "forbid"}

    _validate_url = field_validator("url")(_reject_local_or_private_url)


class PersonalKnowledgeBaseTextPublicUpdate(BaseModel):
    """Full-replace payload for PUT /api/v2/public/personal-kb/{id}/text.

    Unlike PersonalKnowledgeBaseTextUpdate (internal, PATCH-style), both
    `title` and `content` are mandatory - an empty body or a body missing
    either field is rejected instead of silently keeping the existing value.
    """
    title: str = Field(..., min_length=2, max_length=300)
    content: str = Field(..., min_length=2, max_length=300)

    model_config = {"extra": "forbid"}

    _validate_title = field_validator("title")(_non_blank)
    _validate_content = field_validator("content")(_non_blank)


class PersonalKnowledgeBaseResponse(BaseModel):
    id: int
    kb_type: str
    title: Optional[str] = None
    content_path: Optional[str] = None
    content_text: Optional[str] = None
    file_size: Optional[float] = None
    num_chunks: int = 0
    agent_count: int = 0
    created_at: datetime
    modified_at: datetime

    class Config:
        from_attributes = True


class PublicPersonalKnowledgeBaseResponse(BaseModel):
    """List-item shape for GET /api/v2/public/personal-kb.

    Narrower than PersonalKnowledgeBaseResponse (used by the internal
    dashboard router): drops `content_text` (not worth repeating in full for
    every row of a list - see PublicPersonalKnowledgeBaseDetailResponse for
    single-item responses, which do include it) and reports file size as
    `file_size_kb` so the unit is unambiguous, instead of a bare `file_size`
    number.
    """

    id: int
    kb_type: str
    title: Optional[str] = None
    content_path: Optional[str] = None
    file_size_kb: Optional[float] = None
    num_chunks: int = 0
    agent_count: int = 0
    created_at: datetime
    modified_at: datetime

    class Config:
        from_attributes = True


class PublicPersonalKnowledgeBaseDetailResponse(PublicPersonalKnowledgeBaseResponse):
    """Single-item shape for the public API's personal-kb endpoints (GET by
    id, and every create/update endpoint) - same as
    PublicPersonalKnowledgeBaseResponse but with `content_text` included, so
    a text/url item's actual content is visible without a second call."""

    content_text: Optional[str] = None


class PersonalKnowledgeBaseAgentItem(BaseModel):
    agent_id: int
    agent_name: str


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
