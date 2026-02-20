from pydantic import BaseModel,AnyHttpUrl
from typing import Optional,Literal
from app_v2.schemas.enum_types import WidgetPosition

class AppearanceConfig(BaseModel):
    widget_title: str | None = None
    widget_subtitle: str | None = None
    primary_color: str = "#562C7C"
    position: WidgetPosition
    show_branding: bool = True

class CustomFieldSchema(BaseModel):
    field_name: str
    required: bool = False
    field_type: Literal["text", "number", "email", "textarea"] = "text"


class PrechatConfig(BaseModel):
    enable_prechat: bool = False
    require_name: bool = False
    require_email: bool = False
    require_phone: bool = False
    custom_fields: list[CustomFieldSchema] = []



class WebAgentConfig(BaseModel):
    web_agent_name: str
    agent_id: int

    appearance: AppearanceConfig
    prechat: PrechatConfig



class WebAgentConfigResponse(BaseModel):
    id: int
    public_id: str
    web_agent_name: str
    shareable_link: str
    agent_name: str
    is_enabled: Optional[bool]

    appearance: AppearanceConfig
    prechat: PrechatConfig

    model_config = {"from_attributes": True}


class WebAgentListResponse(BaseModel):
    id:int
    web_agent_name: str
    public_id: str
    shareable_link: str
    is_enabled: bool | None = True




class WebAgentPublicConfig(BaseModel):
    public_id: str
    web_agent_name: str
    appearance: AppearanceConfig
    prechat: PrechatConfig

class WebAgentLeadCreate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    custom_data: Optional[list] = None
    conversation_id: Optional[int] = None
