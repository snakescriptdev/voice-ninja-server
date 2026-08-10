from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Optional, Dict
from datetime import datetime, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .built_in_tools import BuiltInToolsParams, PublicBuiltInToolsParams
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
        raise ValueError(f"'{value}' is not a valid timezone. Please use a standard timezone name, e.g. 'America/New_York' or 'Asia/Kolkata'.")
    return value


def _validate_variables(value: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not value:
        return value
    for key, val in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Variable keys cannot be empty or contain only spaces")
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Variable '{key}' cannot be empty or contain only spaces")
    return value


def _validate_id_list(value, field_label: str):
    """Validates a knowledgebase/tools list entry-by-entry with a clear
    message, running in `mode="before"` so it runs ahead of Pydantic's
    built-in `int | Dict` union validation — which, for a bad entry (e.g.
    `null`), otherwise reports every failed union member at once (e.g. both
    "Input should be a valid integer" and "Input should be a valid
    dictionary") instead of one understandable message.
    """
    if value is None:
        return value
    if not isinstance(value, list):
        raise ValueError(f"{field_label} must be a list of ids")

    ids = []
    for item in value:
        item_id = item.get("id") if isinstance(item, dict) else item
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            raise ValueError(
                f"{field_label} contains an invalid entry — each entry must be a numeric id"
            )
        ids.append(item_id)

    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        duplicate_list = ", ".join(str(d) for d in duplicates)
        raise ValueError(f"{field_label} contains duplicate id(s): {duplicate_list}. Each id must be listed only once.")

    return value


def _validate_knowledgebase_list(value):
    return _validate_id_list(value, "knowledgebase")


def _validate_tools_list(value):
    return _validate_id_list(value, "tools")


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
    _validate_variables = field_validator("variables")(_validate_variables)


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
    _validate_variables = field_validator("variables")(_validate_variables)


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
    tools: List[dict[str,int|str|bool]] = []
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

# A single, complete, hand-written example — auto-synthesized-per-field
# examples are unreliable for union/list-of-int-or-object fields (some
# were rendering as empty/missing in Postman collections generated from
# openapi.json), so this is attached directly as the request body
# example instead of relying on that.
_PUBLIC_AGENT_PAYLOAD_EXAMPLE = {
    "agent_name": "Sales Assistant",
    "first_message": "Hi! How can I help you today?",
    "system_prompt": "You are a helpful sales assistant for {{company_name}}.",
    "voice": 3508,
    "ai_model": 7786,
    "language": 1946,
    "knowledgebase": [101, 102],
    "variables": {"key_1": "value_1", "key_2": "value_2"},
    "tools": [201, 202],
    "built_in_tools": {
        "end_call": True,
        "transfer_to_agent": [
            {"agent_id": 42, "condition": "User asks for the sales department"},
            {"agent_id": 57, "condition": "User asks for billing or a refund"},
        ],
    },
    "timezone": "America/New_York",
}

# PublicAgentUpdate-only variant of the example above — adds `is_enabled`,
# which PublicAgentCreate has no field for (agents are always created
# enabled), so it can't go in the shared example without misleadingly
# suggesting it's accepted on create too.
_PUBLIC_AGENT_UPDATE_PAYLOAD_EXAMPLE = {**_PUBLIC_AGENT_PAYLOAD_EXAMPLE, "is_enabled": True}


class PublicAgentCreate(BaseModel):
    # Reject any field not listed below instead of silently ignoring it —
    # a typo'd or unsupported field name in the request body should surface
    # as an error, not disappear.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_PUBLIC_AGENT_PAYLOAD_EXAMPLE]})

    agent_name: str = Field(..., description="(string) Display name for the agent — plain text, not an id. Must be unique per account (case-insensitive).")
    first_message: str = Field(..., min_length=1, description="(string) Opening line the agent speaks when a call starts — free-form text. Required, cannot be blank/whitespace-only.")
    system_prompt: str = Field(..., description="(string) The agent's system prompt — free-form text. May reference `{{variable_name}}` placeholders, which populate `variables` automatically.")
    voice: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/voices response item — not its `voice_name`. Must be an enabled voice that has sample audio available.")
    ai_model: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/ai-models response item — not its `model_name`. The `custom-llm` model cannot be used to create an agent via this API.")
    language: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/languages response item — not its `lang_code`.")
    knowledgebase: Optional[List[int | Dict]] = Field(default=[], description="(list of numeric ids, NOT strings) Personal knowledge base items to attach — either a list of integer ids or a list of objects shaped `{\"id\": <int>}`. Each id is the `id` field from a GET /api/v2/public/personal-kb response item.", examples=[[101, 102]])
    variables: Optional[Dict[str, str]] = Field(default={}, description="(object of string: string) Dynamic variables for the agent, e.g. `{\"key_1\": \"value_1\"}` — keys and values are both strings. Values cannot be empty or whitespace-only.", examples=[{"key_1": "value_1", "key_2": "value_2"}])
    tools: Optional[List[int | Dict]] = Field(default=[], description="(list of numeric ids, NOT strings) Tool ids to attach — either a list of integer ids or a list of objects shaped `{\"id\": <int>}`. Each id is the `id` field from a GET /api/v2/public/functions response item.", examples=[[201, 202]])
    built_in_tools: Optional[PublicBuiltInToolsParams] = Field(default=None, description="(nested object, not a string or id) Configuration for built-in tools. Only end_call and transfer_to_agent are supported via this API.")
    timezone: Optional[str] = Field(default=None, description="(string, not an id) IANA timezone name for the agent, e.g. 'America/New_York' — required only if `system_prompt` uses a `{{system__time}}`-style placeholder.")

    _validate_timezone = field_validator("timezone")(_validate_timezone)
    _validate_agent_name = field_validator("agent_name")(validate_entity_name)
    _validate_first_message = field_validator("first_message")(_validate_first_message)
    _validate_variables = field_validator("variables")(_validate_variables)
    _validate_knowledgebase = field_validator("knowledgebase", mode="before")(_validate_knowledgebase_list)
    _validate_tools = field_validator("tools", mode="before")(_validate_tools_list)


class PublicAgentUpdate(BaseModel):
    """
    Payload for PUT /api/v2/public/agents/{agent_id}.

    No `phone` — phone-to-agent assignment only happens from the /phone
    dashboard page now, not inline at agent create/update (same reasoning
    as dropping `twilio_connector_id` below).

    This is a true PUT, not the internal router's PATCH-style AgentUpdate:
    agent_name/first_message/system_prompt/voice/ai_model/language are
    required on every call (identical to PublicAgentCreate) and are always
    applied — omitting one is a validation error rather than silently
    leaving the old value in place.

    knowledgebase/tools/variables/built_in_tools stay partial-update
    (omit to leave unchanged): ElevenLabsAgent.update_agent only overrides
    non-None parameters, so it has no way to distinguish "clear this list"
    from "leave it alone" — forcing full-replace here would silently fail
    to actually clear anything on the ElevenLabs side.

    No `twilio_connector_id` — public callers can't specify one (same as
    PublicAgentCreate).
    """
    # Reject any field not listed below instead of silently ignoring it —
    # a typo'd or unsupported field name in the request body should surface
    # as an error, not disappear.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_PUBLIC_AGENT_UPDATE_PAYLOAD_EXAMPLE]})

    agent_name: str = Field(..., description="(string) Display name for the agent — plain text, not an id. Must be unique per account (case-insensitive).")
    first_message: str = Field(..., min_length=1, description="(string) Opening line the agent speaks when a call starts — free-form text. Required, cannot be blank/whitespace-only.")
    system_prompt: str = Field(..., description="(string) The agent's system prompt — free-form text. May reference `{{variable_name}}` placeholders, which populate `variables` automatically.")
    voice: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/voices response item — not its `voice_name`. Must be an enabled voice that has sample audio available.")
    ai_model: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/ai-models response item — not its `model_name`. The `custom-llm` model cannot be used for an agent via this API.")
    language: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/languages response item — not its `lang_code`.")
    knowledgebase: Optional[List[int | Dict]] = Field(default=None, description="(list of numeric ids, NOT strings) Personal knowledge base items to attach — either a list of integer ids or a list of objects shaped `{\"id\": <int>}`. Each id is the `id` field from a GET /api/v2/public/personal-kb response item. Omit to leave the current attachments unchanged.", examples=[[101, 102]])
    variables: Optional[Dict[str, str]] = Field(default=None, description="(object of string: string) Dynamic variables for the agent, e.g. `{\"key_1\": \"value_1\"}` — keys and values are both strings. Omit to leave unchanged. Values cannot be empty or whitespace-only.", examples=[{"key_1": "value_1", "key_2": "value_2"}])
    tools: Optional[List[int | Dict]] = Field(default=None, description="(list of numeric ids, NOT strings) Tool ids to attach — either a list of integer ids or a list of objects shaped `{\"id\": <int>}`. Each id is the `id` field from a GET /api/v2/public/functions response item. Omit to leave the current tools unchanged.", examples=[[201, 202]])
    built_in_tools: Optional[PublicBuiltInToolsParams] = Field(default=None, description="(nested object, not a string or id) Configuration for built-in tools. Only end_call and transfer_to_agent are supported via this API. Omit to leave unchanged.")
    timezone: Optional[str] = Field(default=None, description="(string, not an id) IANA timezone name for the agent, e.g. 'America/New_York'. Omit or send null to clear.")
    is_enabled: Optional[bool] = Field(default=None, description="(boolean) Whether the agent is enabled. Omit to leave unchanged. Setting this to `false` also disables all of this agent's widgets and web agent pages; setting it back to `true` re-enables them.")

    _validate_timezone = field_validator("timezone")(_validate_timezone)
    _validate_agent_name = field_validator("agent_name")(validate_entity_name)
    _validate_first_message = field_validator("first_message")(_validate_first_message)
    _validate_variables = field_validator("variables")(_validate_variables)
    _validate_knowledgebase = field_validator("knowledgebase", mode="before")(_validate_knowledgebase_list)
    _validate_tools = field_validator("tools", mode="before")(_validate_tools_list)


class PublicAgentRead(BaseModel):
    id: int
    agent_name: str
    is_enabled: bool
    first_message: str | None
    system_prompt: str
    voice: int
    voice_name: str
    created_at: datetime
    updated_at: datetime
    ai_model: int
    ai_model_name: str
    language: int
    language_name: str
    knowledgebase: List[dict[str,int|str]] = []
    variables: Dict[str, str] = {}
    tools: List[dict[str,int|str]] = []
    built_in_tools: Optional[Dict] = None
    timezone: Optional[str] = None
    is_first_call_pending: bool = True
    # Only populated when is_first_call_pending is True — the admin-configured
    # safety cap (seconds) on this agent's very first call. None once the
    # agent has had a prior call, since the cap no longer applies.
    first_call_max_duration_seconds: Optional[int] = None
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
    voice: int
    voice_name: str
    created_at: datetime
    updated_at: datetime
    ai_model: int
    ai_model_name: str
    language: int
    language_name: str
    timezone: Optional[str] = None
    is_first_call_pending: bool = True
    kb_count: int = 0
    tool_count: int = 0
    conversation_count: int = 0
    credits_used: int = 0
    leads_count: int = 0
    class Config:
        from_attributes = True

