import re
from pydantic import BaseModel,AnyHttpUrl, field_validator, Field, ConfigDict
from typing import Optional,Literal,List
from app_v2.schemas.enum_types import WidgetPosition
from app_v2.utils.validation_utils import validate_entity_name, validate_entity_name_optional
from datetime import datetime

class AppearanceConfig(BaseModel):
    widget_title: str | None = None
    widget_subtitle: str | None = None
    primary_color: str = "#562C7C"
    position: WidgetPosition
    show_branding: bool = True


class AppearanceConfigUpdate(BaseModel):
    widget_title: Optional[str] = None
    widget_subtitle: Optional[str] = None
    primary_color: Optional[str] = None
    position: Optional[WidgetPosition] = None
    show_branding: Optional[bool] = None

class CustomFieldSchema(BaseModel):
    field_name: str
    required: bool = False
    field_type: Literal["text", "number", "email", "textarea","phone"] = "text"


class PrechatConfig(BaseModel):
    enable_prechat: bool = False
    require_name: bool = False
    require_email: bool = False
    require_phone: bool = False
    custom_fields: list[CustomFieldSchema] = []


class PrechatConfigUpdate(BaseModel):
    enable_prechat: Optional[bool] = None
    require_name: Optional[bool] = None
    require_email: Optional[bool] = None
    require_phone: Optional[bool] = None
    custom_fields: Optional[list[CustomFieldSchema]] = None



class WidgetConfig(BaseModel):
    widget_name: str
    agent_id: int

    appearance: AppearanceConfig
    prechat: PrechatConfig

    _validate_widget_name = field_validator("widget_name")(validate_entity_name)



class WidgetConfigResponse(BaseModel):
    id: int
    public_id: str
    widget_name: str
    shareable_link: str
    agent_id: int
    is_enabled: Optional[bool]

    appearance: AppearanceConfig
    prechat: PrechatConfig

    model_config = {"from_attributes": True}


class WidgetListResponse(BaseModel):
    id:int
    widget_name: str
    public_id: str
    shareable_link: str
    is_enabled: bool | None = True
    created_at: datetime
    updated_at: datetime
    agent_id: int | None = None
    agent_name: str



class WidgetPublicConfig(BaseModel):
    public_id: str
    widget_name: str
    appearance: AppearanceConfig
    prechat: PrechatConfig

class WidgetLeadCreate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    custom_data: Optional[list] = None

class WidgetConfigUpdate(BaseModel):
    widget_name: Optional[str] = None
    agent_id: Optional[int] = None

    appearance: Optional[AppearanceConfigUpdate] = None
    prechat: Optional[PrechatConfigUpdate] = None
    is_enabled: Optional[bool] = None

    _validate_widget_name = field_validator("widget_name")(validate_entity_name_optional)


# -------------------------------------------------------------------
# Public API (app_v2/routers/public_api.py) only. Kept separate from
# AppearanceConfig/PrechatConfig/WidgetConfig above (shared with the
# internal, JWT-authenticated widget_config.py router the frontend calls)
# so the stricter validation/defaults/docs below don't change what the
# internal dashboard accepts.
# -------------------------------------------------------------------

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _validate_hex_color(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not _HEX_COLOR_RE.match(value.strip()):
        raise ValueError("must be a valid hex color code, e.g. #AC1E7A or #ABC")
    return value


def _validate_non_blank_optional_text(value: Optional[str]) -> Optional[str]:
    """None (field omitted/cleared) is fine; an explicit empty or
    whitespace-only string is rejected instead of silently accepted."""
    if value is None:
        return None
    if not value.strip():
        raise ValueError("cannot be empty or only spaces")
    return value


class PublicCustomFieldSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_name: str = Field(..., description="(string) Label for this custom prechat field, shown to the visitor before the call starts, e.g. 'Company Name'.")
    required: bool = Field(default=False, description="(boolean) Whether the visitor must fill this field in before starting a call.")
    field_type: Literal["text", "number", "email", "textarea", "phone"] = Field(default="text", description="(string) Input type rendered for this field. One of: text, number, email, textarea, phone.")


class PublicAppearanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget_title: Optional[str] = Field(default=None, description="(string) Title shown at the top of the widget. Omit or send null for no title — cannot be an empty/whitespace-only string.")
    widget_subtitle: Optional[str] = Field(default=None, description="(string) Subtitle shown under the title. Omit or send null for no subtitle — cannot be an empty/whitespace-only string.")
    primary_color: str = Field(default="#AC1E7A", description="(string) Hex color code for the widget's accent color, e.g. '#AC1E7A' or '#ABC'.")
    position: WidgetPosition = Field(..., description="(string) Where the widget bubble docks on the page. One of: top-right, top-left, bottom-right, bottom-left.")
    show_branding: bool = Field(default=True, description='(boolean) When true, displays the text "Powered by Voice Ninja" on the widget.')

    _validate_color = field_validator("primary_color")(_validate_hex_color)
    _validate_title = field_validator("widget_title")(_validate_non_blank_optional_text)
    _validate_subtitle = field_validator("widget_subtitle")(_validate_non_blank_optional_text)


class PublicAppearanceConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget_title: Optional[str] = Field(default=None, description="(string) Title shown at the top of the widget — cannot be an empty/whitespace-only string. Omit to leave unchanged.")
    widget_subtitle: Optional[str] = Field(default=None, description="(string) Subtitle shown under the title — cannot be an empty/whitespace-only string. Omit to leave unchanged.")
    primary_color: Optional[str] = Field(default=None, description="(string) Hex color code for the widget's accent color, e.g. '#AC1E7A' or '#ABC'. Omit to leave unchanged.")
    position: Optional[WidgetPosition] = Field(default=None, description="(string) One of: top-right, top-left, bottom-right, bottom-left. Omit to leave unchanged.")
    show_branding: Optional[bool] = Field(default=None, description='(boolean) When true, displays the text "Powered by Voice Ninja" on the widget. Omit to leave unchanged.')

    _validate_color = field_validator("primary_color")(_validate_hex_color)
    _validate_title = field_validator("widget_title")(_validate_non_blank_optional_text)
    _validate_subtitle = field_validator("widget_subtitle")(_validate_non_blank_optional_text)


class PublicPrechatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable_prechat: bool = Field(default=False, description="(boolean) Whether to show a prechat form before the call starts.")
    require_name: bool = Field(default=False, description="(boolean) Whether the visitor must enter their name in the prechat form.")
    require_email: bool = Field(default=False, description="(boolean) Whether the visitor must enter their email in the prechat form.")
    require_phone: bool = Field(default=False, description="(boolean) Whether the visitor must enter their phone number in the prechat form.")
    custom_fields: List[PublicCustomFieldSchema] = Field(
        default=[],
        description="(list of objects) Extra prechat form fields beyond name/email/phone — fully optional, add or remove based on what this widget needs. Each item is `{\"field_name\": <string>, \"required\": <bool>, \"field_type\": <one of text/number/email/textarea/phone>}`.",
        examples=[[{"field_name": "Company Name", "required": False, "field_type": "text"}]],
    )


class PublicPrechatConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable_prechat: Optional[bool] = Field(default=None, description="(boolean) Whether to show a prechat form before the call starts. Omit to leave unchanged.")
    require_name: Optional[bool] = Field(default=None, description="(boolean) Whether the visitor must enter their name in the prechat form. Omit to leave unchanged.")
    require_email: Optional[bool] = Field(default=None, description="(boolean) Whether the visitor must enter their email in the prechat form. Omit to leave unchanged.")
    require_phone: Optional[bool] = Field(default=None, description="(boolean) Whether the visitor must enter their phone number in the prechat form. Omit to leave unchanged.")
    custom_fields: Optional[List[PublicCustomFieldSchema]] = Field(
        default=None,
        description="(list of objects) Extra prechat form fields beyond name/email/phone — fully optional, add or remove based on what this widget needs. Each item is `{\"field_name\": <string>, \"required\": <bool>, \"field_type\": <one of text/number/email/textarea/phone>}`. Omit to leave unchanged; send `[]` to clear.",
        examples=[[{"field_name": "Company Name", "required": False, "field_type": "text"}]],
    )


_PUBLIC_WIDGET_PAYLOAD_EXAMPLE = {
    "widget_name": "Sales Widget",
    "agent_id": 42,
    "appearance": {
        "widget_title": "Chat with us",
        "widget_subtitle": "We usually respond right away",
        "primary_color": "#AC1E7A",
        "position": "bottom-right",
        "show_branding": True,
    },
    "prechat": {
        "enable_prechat": True,
        "require_name": True,
        "require_email": False,
        "require_phone": False,
        "custom_fields": [
            {"field_name": "Company Name", "required": False, "field_type": "text"}
        ],
    },
}


class PublicWidgetConfig(BaseModel):
    # Reject any field not listed below instead of silently ignoring it —
    # a typo'd or unsupported field name in the request body should surface
    # as an error, not disappear.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_PUBLIC_WIDGET_PAYLOAD_EXAMPLE]})

    widget_name: str = Field(..., description="(string) Display name for the widget. Must be unique per agent (case-insensitive).")
    agent_id: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/agents response item.")
    appearance: PublicAppearanceConfig
    prechat: PublicPrechatConfig

    _validate_widget_name = field_validator("widget_name")(validate_entity_name)


class PublicWidgetConfigUpdate(BaseModel):
    # Reject any field not listed below instead of silently ignoring it —
    # a typo'd or unsupported field name in the request body should surface
    # as an error, not disappear.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{**_PUBLIC_WIDGET_PAYLOAD_EXAMPLE, "is_enabled": True}]})

    widget_name: Optional[str] = Field(default=None, description="(string) Display name for the widget. Must be unique per agent (case-insensitive). Omit to leave unchanged.")
    agent_id: Optional[int] = Field(default=None, description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/agents response item. Omit to leave unchanged.")
    appearance: Optional[PublicAppearanceConfigUpdate] = Field(default=None, description="Omit to leave unchanged.")
    prechat: Optional[PublicPrechatConfigUpdate] = Field(default=None, description="Omit to leave unchanged.")
    is_enabled: Optional[bool] = Field(default=None, description="(boolean) Whether the widget is enabled. Omit to leave unchanged.")

    _validate_widget_name = field_validator("widget_name")(validate_entity_name_optional)