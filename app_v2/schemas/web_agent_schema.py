from pydantic import BaseModel, field_serializer
from typing import Optional
from datetime import datetime
from app_v2.schemas.enum_types import WebAgentPosition


class WebAgentCreate(BaseModel):
    web_agent_name: str
    agent_id: int
    widget_id: int
    bg_color: str = "#0B0B0F"
    agent_position: WebAgentPosition = WebAgentPosition.center


class WebAgentUpdate(BaseModel):
    web_agent_name: Optional[str] = None
    agent_id: Optional[int] = None
    widget_id: Optional[int] = None
    bg_color: Optional[str] = None
    agent_position: Optional[WebAgentPosition] = None
    is_enabled: Optional[bool] = None


class WebAgentResponse(BaseModel):
    id: int
    public_id: str
    web_agent_name: str
    agent_id: int
    agent_name: str
    widget_id: int
    widget_name: str
    is_enabled: bool
    bg_color: str
    agent_position: WebAgentPosition
    shareable_link: str
    created_at: datetime

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
    shareable_link: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_datetime(self, dt: datetime):
        return dt.date()
