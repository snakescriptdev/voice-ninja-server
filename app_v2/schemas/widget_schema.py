import re
from pydantic import BaseModel,AnyHttpUrl, field_validator, model_validator, Field, ConfigDict
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
    agent_name: str
    is_enabled: Optional[bool]
    created_at: datetime
    updated_at: datetime

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


def require_flag_disabled_error(field_name: str, human: str) -> str:
    return (
        f"`{field_name}` can't be enabled when `enable_prechat` is disabled. Since the "
        f"pre-chat form isn't shown to visitors, requiring {human} has no effect."
    )


def custom_fields_disabled_error() -> str:
    return (
        "`custom_fields` can't be set when `enable_prechat` is disabled. Since the "
        "pre-chat form isn't shown to visitors, custom fields have no effect."
    )


REQUIRE_FLAG_HUMAN_LABELS = (
    ("require_name", "a name"),
    ("require_email", "an email"),
    ("require_phone", "a phone number"),
)


def _validate_non_blank_optional_text(value: Optional[str]) -> Optional[str]:
    """None (field omitted/cleared) is fine; an explicit empty or
    whitespace-only string is rejected instead of silently accepted."""
    if value is None:
        return None
    if not value.strip():
        raise ValueError("cannot be empty or only spaces")
    return value


def _validate_unique_custom_field_names(fields):
    """Reject two custom fields whose field_name only differs by case/whitespace
    (e.g. "Company Name" and "company name ") — they'd collide as the same
    prechat field, so a differing field_type between them is silently
    meaningless rather than an intentional second field."""
    if not fields:
        return fields
    seen = {}
    for f in fields:
        key = f.field_name.strip().lower()
        if key in seen:
            raise ValueError(
                f"Duplicate custom field name '{f.field_name}' (matches '{seen[key]}') — "
                "custom field names must be unique, case-insensitive."
            )
        seen[key] = f.field_name
    return fields


class PublicCustomFieldSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_name: str = Field(..., description="(string) Label for this custom prechat field, shown to the visitor before the call starts, e.g. 'Company Name'.")
    required: bool = Field(default=False, description="(boolean) Whether the visitor must fill this field in before starting a call.")
    # Allowed field_type values: text, number, email, textarea, phone. This
    # controls which input widget is rendered for the field in the prechat form.
    field_type: Literal["text", "number", "email", "textarea", "phone"] = Field(default="text", description="(string) Input type rendered for this field. Allowed values: text, number, email, textarea, phone.")


class PublicAppearanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget_title: Optional[str] = Field(default=None, description="(string) Title shown at the top of the widget. Omit or send null for no title — cannot be an empty/whitespace-only string.")
    widget_subtitle: Optional[str] = Field(default=None, description="(string) Subtitle shown under the title. Omit or send null for no subtitle — cannot be an empty/whitespace-only string.")
    primary_color: str = Field(..., description="(string) Hex color code for the widget's accent color, e.g. '#AC1E7A' or '#ABC'.")
    position: WidgetPosition = Field(..., description="(string) Where the widget bubble docks on the page. One of: top-right, top-left, bottom-right, bottom-left.")
    show_branding: bool = Field(default=True, description='(boolean) When true, displays the text "Powered by Voice Ninja" on the widget.')

    _validate_color = field_validator("primary_color")(_validate_hex_color)
    _validate_title = field_validator("widget_title")(_validate_non_blank_optional_text)
    _validate_subtitle = field_validator("widget_subtitle")(_validate_non_blank_optional_text)


class PublicPrechatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable_prechat: bool = Field(..., description="(boolean) Whether to show a prechat form before the call starts. `require_name`/`require_email`/`require_phone` only take effect when this is true.")
    require_name: bool = Field(default=False, description="(boolean) Whether the visitor must enter their name in the prechat form. Must be false when `enable_prechat` is false. Defaults to true when `enable_prechat` is true and this is omitted.")
    require_email: bool = Field(default=False, description="(boolean) Whether the visitor must enter their email in the prechat form. Must be false when `enable_prechat` is false. Defaults to true when `enable_prechat` is true and this is omitted.")
    require_phone: bool = Field(default=False, description="(boolean) Whether the visitor must enter their phone number in the prechat form. Must be false when `enable_prechat` is false. Defaults to false when omitted.")
    # NOTE: the custom field(s) below (e.g. "Company Name") are just a sample —
    # they are entirely based on your own use case. Add, remove, or rename as
    # many custom fields as you need. Allowed field_type values: text, number,
    # email, textarea, phone.
    custom_fields: List[PublicCustomFieldSchema] = Field(
        default=[],
        description="(list of objects) Extra prechat form fields beyond name/email/phone — fully optional, add/remove/rename as many as you need based on your own use case. Each item is `{\"field_name\": <string>, \"required\": <bool>, \"field_type\": <one of text/number/email/textarea/phone>}`. Allowed field_type values: text, number, email, textarea, phone. Must be empty/omitted when `enable_prechat` is false.",
        examples=[[{"field_name": "Company Name", "required": False, "field_type": "text"}]],
    )

    _validate_unique_custom_fields = field_validator("custom_fields")(_validate_unique_custom_field_names)

    @model_validator(mode="after")
    def _validate_and_default_require_flags(self):
        if self.enable_prechat:
            explicit = self.model_fields_set
            # require_name/require_email default to true (require_phone stays
            # false) when the caller enables prechat without saying which
            # fields to require — a bare enable_prechat=true otherwise silently
            # required nothing.
            if "require_name" not in explicit:
                self.require_name = True
            if "require_email" not in explicit:
                self.require_email = True
        else:
            for field_name, human in REQUIRE_FLAG_HUMAN_LABELS:
                if getattr(self, field_name):
                    raise ValueError(require_flag_disabled_error(field_name, human))
            if self.custom_fields:
                raise ValueError(custom_fields_disabled_error())
        return self


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

# Raw, hand-formatted JSON (with real "//" comments) used ONLY as the
# request body example shown in Swagger/Postman for POST and PUT /widgets
# (wired in via `openapi_extra` in public_api.py). This is kept as a plain
# string — not a dict — specifically so the "//" comments render as visible
# comments in the generated docs/Postman body instead of becoming literal
# JSON fields that the API would reject. Anyone sending a real request must
# strip the comment lines first, since they're not valid JSON.
PUBLIC_CREATE_WIDGET_BODY_EXAMPLE = """{
  "widget_name": "Sales Widget",
  "agent_id": 42,
  "appearance": {
    "widget_title": "Chat with us",
    "widget_subtitle": "We usually respond right away",
    "primary_color": "#AC1E7A",
    //allowed values in position field  are top-left,top-right,bottom-right,bottom-left.
    "position": "bottom-right",
    // show_branding: when true, displays the text "Powered by Voice Ninja" on the widget.
    "show_branding": true
  },
  "prechat": {
    "enable_prechat": true,
    "require_name": true,
    "require_email": false,
    "require_phone": false,
    // custom_fields below is just a sample based on "Company Name" — add, remove, or
    // rename as many custom fields as you need for your own use case.
    // Allowed field_type values: text, number, email, textarea, phone.
    "custom_fields": [
      {"field_name": "Company Name", "required": false, "field_type": "text"}
    ]
  }
}"""

PUBLIC_UPDATE_WIDGET_BODY_EXAMPLE = """{
  "widget_name": "Sales Widget",
  "agent_id": 42,
  "is_enabled": true,
  "appearance": {
    "widget_title": "Chat with us",
    "widget_subtitle": "We usually respond right away",
    "primary_color": "#AC1E7A",
    //allowed values in position field  are top-left,top-right,bottom-right,bottom-left.
    "position": "bottom-right",
    // show_branding: when true, displays the text "Powered by Voice Ninja" on the widget.
    "show_branding": true
  },
  "prechat": {
    "enable_prechat": true,
    "require_name": true,
    "require_email": false,
    "require_phone": false,
    // custom_fields below is just a sample based on "Company Name" — add, remove, or
    // rename as many custom fields as you need for your own use case.
    // Allowed field_type values: text, number, email, textarea, phone.
    // This is a full PUT: custom_fields (like the rest of `prechat`/`appearance`)
    // is replaced wholesale with whatever you send here — omit it (or send [])
    // for no custom fields, it will NOT keep whatever was previously saved.
    "custom_fields": [
      {"field_name": "Company Name", "required": false, "field_type": "text"}
    ]
  }
}"""


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
    """
    Payload for PUT /api/v2/public/widgets/{public_id}.

    True PUT, not a PATCH: every field below is required on every call
    (identical shape and validation to PublicWidgetConfig, used by POST
    /widgets) and always fully replaces the widget's current state —
    omitting one is a validation error rather than silently leaving the old
    value in place.
    """
    # Reject any field not listed below instead of silently ignoring it —
    # a typo'd or unsupported field name in the request body should surface
    # as an error, not disappear.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{**_PUBLIC_WIDGET_PAYLOAD_EXAMPLE, "is_enabled": True}]})

    widget_name: str = Field(..., description="(string) Display name for the widget. Must be unique per agent (case-insensitive).")
    agent_id: int = Field(..., description="(numeric id, NOT a string) The `id` field from a GET /api/v2/public/agents response item.")
    appearance: PublicAppearanceConfig
    prechat: PublicPrechatConfig
    is_enabled: bool = Field(..., description="(boolean) Whether the widget is enabled.")

    _validate_widget_name = field_validator("widget_name")(validate_entity_name)