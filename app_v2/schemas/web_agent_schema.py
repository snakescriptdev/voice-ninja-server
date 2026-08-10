from pydantic import BaseModel, Field, field_serializer, field_validator
from typing import Optional
from datetime import datetime
from app_v2.schemas.enum_types import WebAgentPosition
from app_v2.utils.validation_utils import (
    validate_entity_name,
    validate_entity_name_optional,
    validate_hex_color,
    validate_hex_color_optional,
)

_POSITION_DESCRIPTION = (
    "Position of the web agent's call button/panel on the hosted page. "
    "Allowed values: left, center, right."
)


class WebAgentCreate(BaseModel):
    web_agent_name: str
    agent_id: int
    widget_id: int
    bg_color: str = "#0B0B0F"
    agent_position: WebAgentPosition = Field(WebAgentPosition.center, description=_POSITION_DESCRIPTION)

    model_config = {"extra": "forbid"}

    _validate_web_agent_name = field_validator("web_agent_name")(validate_entity_name)
    _validate_bg_color = field_validator("bg_color")(validate_hex_color)


class WebAgentUpdate(BaseModel):
    web_agent_name: Optional[str] = None
    agent_id: Optional[int] = None
    widget_id: Optional[int] = None
    bg_color: Optional[str] = None
    agent_position: Optional[WebAgentPosition] = Field(None, description=_POSITION_DESCRIPTION)
    is_enabled: Optional[bool] = None

    _validate_web_agent_name = field_validator("web_agent_name")(validate_entity_name_optional)
    _validate_bg_color = field_validator("bg_color")(validate_hex_color_optional)


class WebAgentPublicUpdate(BaseModel):
    """Full-replace payload for PUT /api/v2/public/web-agents/{public_id}.

    Unlike WebAgentUpdate (used by the internal dashboard PUT, which behaves
    like a PATCH), this requires the same identity/config fields POST does —
    an empty body or a body missing web_agent_name/agent_id/widget_id is
    rejected instead of silently keeping the existing values. `is_enabled` is
    a status toggle rather than a "shape" field, so it stays optional and
    preserves the current value when omitted.
    """

    web_agent_name: str
    agent_id: int
    widget_id: int
    bg_color: str = "#0B0B0F"
    agent_position: WebAgentPosition = Field(WebAgentPosition.center, description=_POSITION_DESCRIPTION)
    is_enabled: Optional[bool] = None

    model_config = {"extra": "forbid"}

    _validate_web_agent_name = field_validator("web_agent_name")(validate_entity_name)
    _validate_bg_color = field_validator("bg_color")(validate_hex_color)


class WebAgentResponse(BaseModel):
    id: int
    public_id: str
    web_agent_name: str
    agent_id: int
    agent_name: str
    widget_id: int
    widget_name: str
    # Pulled from the linked widget so the hosted full-page call experience
    # can share the same accent color / "Powered by Voice Ninja" branding as
    # the widget, without duplicating a whole appearance config on web agent
    # pages themselves.
    widget_primary_color: str
    widget_show_branding: bool
    is_enabled: bool
    bg_color: str
    agent_position: WebAgentPosition
    shareable_link: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WebAgentListResponse(BaseModel):
    id: int
    public_id: str
    web_agent_name: str
    is_enabled: bool
    bg_color: str
    agent_position: WebAgentPosition
    agent_id: int
    agent_name: str
    widget_id: int
    widget_name: str
    widget_primary_color: str
    widget_show_branding: bool
    shareable_link: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_datetime(self, dt: datetime):
        return dt.date()


class PublicWebAgentListResponse(BaseModel):
    """List-item shape for GET /api/v2/public/web-agents.

    Deliberately narrower than WebAgentListResponse: appearance/branding
    fields (bg_color, agent_position, widget_primary_color,
    widget_show_branding) are configured per web agent and only matter when
    reading/editing a single one, so they're left out of the list response.
    """

    id: int
    public_id: str
    web_agent_name: str
    is_enabled: bool
    agent_id: int
    agent_name: str
    widget_id: int
    widget_name: str
    shareable_link: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_datetime(self, dt: datetime):
        return dt.date()
