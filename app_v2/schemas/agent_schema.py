from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict
from datetime import datetime, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .built_in_tools import BuiltInToolsParams
from app_v2.utils.validation_utils import validate_entity_name, validate_entity_name_optional


def _validate_first_message(value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise ValueError("first_message cannot be empty or only spaces.")
    return v


def _validate_timezone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValueError(f"'{value}' is not a valid timezone (must be a valid tzinfo identifier, e.g. 'America/New_York')")
    return value


class AgentCreate(BaseModel):
    agent_name: str
    first_message: str | None = None
    system_prompt: str
    phone: Optional[str] = Field(None, description="Phone number to assign to this agent (e.g., +14155551234)")
    twilio_connector_id: Optional[int] = Field(None, description="Twilio connector to verify/import `phone` from")
    voice: str
    ai_model: str
    language: str = Field(description="language code to be passed in model (en-01 for english)")
    knowledgebase: Optional[List[int | Dict]] = Field(default=[], description="List of knowledge base IDs or objects")
    variables: Optional[Dict[str, str]] = Field(default={}, description="Dynamic variables for the agent")
    tools: Optional[List[int | Dict]] = Field(default=[], description="List of function/tool IDs or objects")
    built_in_tools: Optional[BuiltInToolsParams] = Field(default=None, description="Configuration for built-in tools")
    timezone: Optional[str] = Field(default=None, description="IANA timezone for the agent (must be valid for tzinfo, e.g. 'America/New_York')")

    _validate_timezone = field_validator("timezone")(_validate_timezone)
    _validate_agent_name = field_validator("agent_name")(validate_entity_name)


class AgentUpdate(BaseModel):
    agent_name: Optional[str] = None
    first_message: Optional[str] = None
    system_prompt: Optional[str] = None
    is_enabled:Optional[bool]=None
    voice: Optional[str] = None
    ai_model: Optional[str] = None
    language: Optional[str] = Field(default=None,description="language code to be passed in model (en-01 for english)")
    phone: Optional[str] = Field(None, description="Phone number to assign to this agent (e.g., +14155551234)")
    twilio_connector_id: Optional[int] = Field(None, description="Twilio connector to verify/import `phone` from")
    knowledgebase: Optional[List[int | Dict]] = None
    variables: Optional[Dict[str, str]] = None
    tools: Optional[List[int | Dict]] = None
    built_in_tools: Optional[BuiltInToolsParams] = None
    timezone: Optional[str] = Field(default=None, description="IANA timezone for the agent (must be valid for tzinfo, e.g. 'America/New_York')")

    _validate_timezone = field_validator("timezone")(_validate_timezone)
    _validate_agent_name = field_validator("agent_name")(validate_entity_name_optional)


class AgentRead(BaseModel):
    id: int
    agent_name: str
    is_enabled:bool
    first_message: str | None
    system_prompt: str
    voice:str
    updated_at: date
    phone: Optional[str] = None
    ai_model: str
    language: str
    elevenlabs_agent_id: Optional[str] = None
    knowledgebase: List[dict[str,int|str]] = []
    variables: Dict[str, str] = {}
    tools: List[dict[str,int|str]] = []
    built_in_tools: Optional[Dict] = None
    timezone: Optional[str] = None
    # True iff this agent has never had a conversation row yet — drives the
    # first-call-duration-cap banner/icon on the frontend (see
    # CoinUsageSettingsModel.first_call_max_duration_seconds).
    is_first_call_pending: bool = True
    kb_count: int = 0
    tool_count: int = 0
    conversation_count: int = 0
    amount_used: float = 0
    leads_count: int = 0
    class Config:
        from_attributes = True


# -------------------------------------------------------------------
# Public API (app_v2/routers/public_api.py) only. Kept separate from
# AgentCreate/AgentRead above because those are shared with the internal
# (JWT-authenticated) agents router the frontend calls.
# -------------------------------------------------------------------

class PublicAgentCreate(BaseModel):
    agent_name: str
    first_message: str = Field(..., min_length=1, description="Opening line the agent speaks when a call starts. Required, cannot be blank/whitespace-only.")
    system_prompt: str
    phone: Optional[str] = Field(None, description="Phone number to assign to this agent (e.g., +14155551234)")
    voice: str = Field(..., description="The `voice_name` value from a GET /api/v2/public/voices item (not its numeric `id`).")
    ai_model: str = Field(..., description="The `model_name` value from a GET /api/v2/public/ai-models item (not its numeric `id`).")
    language: str = Field(description="The `lang_code` value from a GET /api/v2/public/languages item (not its numeric `id`), e.g. 'en' for English.")
    knowledgebase: Optional[List[int | Dict]] = Field(default=[], description="List of knowledge base IDs or objects")
    variables: Optional[Dict[str, str]] = Field(default={}, description="Dynamic variables for the agent")
    tools: Optional[List[int | Dict]] = Field(default=[], description="List of function/tool IDs or objects")
    built_in_tools: Optional[BuiltInToolsParams] = Field(default=None, description="Configuration for built-in tools")
    timezone: Optional[str] = Field(default=None, description="IANA timezone for the agent (must be valid for tzinfo, e.g. 'America/New_York')")

    _validate_timezone = field_validator("timezone")(_validate_timezone)
    _validate_agent_name = field_validator("agent_name")(validate_entity_name)
    _validate_first_message = field_validator("first_message")(_validate_first_message)


class PublicAgentRead(BaseModel):
    id: int
    agent_name: str
    is_enabled: bool
    first_message: str | None
    system_prompt: str
    voice: str
    updated_at: date
    phone: Optional[str] = None
    ai_model: str
    language: str
    knowledgebase: List[dict[str,int|str]] = []
    variables: Dict[str, str] = {}
    tools: List[dict[str,int|str]] = []
    built_in_tools: Optional[Dict] = None
    timezone: Optional[str] = None
    is_first_call_pending: bool = True
    kb_count: int = 0
    tool_count: int = 0
    conversation_count: int = 0
    credits_used: int = 0
    leads_count: int = 0
    class Config:
        from_attributes = True


class PublicAgentListRead(BaseModel):
    id: int
    agent_name: str
    is_enabled: bool
    first_message: str | None
    voice: str
    updated_at: date
    phone: Optional[str] = None
    ai_model: str
    language: str
    timezone: Optional[str] = None
    is_first_call_pending: bool = True
    kb_count: int = 0
    tool_count: int = 0
    conversation_count: int = 0
    credits_used: int = 0
    leads_count: int = 0
    class Config:
        from_attributes = True
