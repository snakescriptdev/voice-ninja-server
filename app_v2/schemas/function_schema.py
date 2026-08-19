import ipaddress
import re
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime
from pydantic import BaseModel, Field, model_validator, field_validator
from enum import Enum
from urllib.parse import urlparse
from app_v2.utils.crypto_utils import decrypt_data


# -------------------------------------------------
# Enums
# -------------------------------------------------

class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ContentType(str, Enum):
    JSON = "application/json"
    FORM = "application/x-www-form-urlencoded"


PrimitiveType = Literal["string", "integer", "number", "boolean"]


# -------------------------------------------------
# Shared validation helpers
# -------------------------------------------------

def _require_non_blank(value: Optional[str], label: str) -> Optional[str]:
    if value is not None and not value.strip():
        raise ValueError(f"{label} cannot be empty or only whitespace")
    return value


def _validate_headers_dict(headers: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    # Messages here deliberately avoid restating "request_headers" (the
    # multi-word snake_case field name) — get_readable_message's generic
    # fallback already prepends the humanized field name ("Request
    # headers"), and since that check is an exact-substring match, a
    # message starting with the literal underscored name doesn't match it
    # and ends up double-prefixed (e.g. "Request headers request_headers...").
    if not headers:
        return headers
    for k, v in headers.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError("cannot contain an empty or whitespace-only key")
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"cannot contain an empty or whitespace-only value for header {k!r}")
    return headers


def _validate_response_variables_dict(value: Any) -> Any:
    # Same rationale as _validate_headers_dict above re: not restating the
    # "response_variables" field name in these messages.
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError(
            "must be an object mapping each variable name to a string "
            "path into the response, e.g. {\"order_status\": \"status\"}"
        )
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError("cannot contain an empty or whitespace-only key")
        if not isinstance(v, str):
            raise ValueError(
                f"the value for {k!r} must be a string path into the response "
                f"(e.g. \"status\"), not a nested object/array"
            )
        if not v.strip():
            raise ValueError(f"cannot contain an empty or whitespace-only value for key {k!r}")
    return value


def _validate_required_against_properties(
    required: Optional[List[str]], properties: Dict[str, Any], label: str
) -> Optional[List[str]]:
    if not required:
        return required
    prefix = f"{label}." if label else ""
    for name in required:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{prefix}required cannot contain an empty or whitespace-only entry")
    missing = [name for name in required if name not in properties]
    if missing:
        raise ValueError(f"{prefix}required references field(s) not present in properties: {missing}")
    return required


_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_function_name(value: str) -> str:
    if not value.strip():
        raise ValueError("name cannot be empty or only whitespace")
    if not _NAME_PATTERN.match(value):
        raise ValueError(
            "name must be snake_case: lowercase letters, numbers, and underscores only, "
            "starting with a letter (e.g. \"get_weather_forecast\")"
        )
    return value


# Loopback/private hosts a tool's URL must never point to (SSRF-adjacent —
# these can never be reachable from ElevenLabs' side anyway).
_BLOCKED_HOSTNAMES = {"localhost"}


def _validate_api_url(v: str) -> str:
    parsed = urlparse(v)
    if parsed.scheme != "https":
        raise ValueError("URL must use https:// — http:// is not allowed")
    if not parsed.netloc:
        raise ValueError("URL must have a valid domain")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL must have a valid domain")
    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        raise ValueError("URL cannot point to localhost")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified):
        raise ValueError("URL cannot point to a private or loopback address")
    return v


# -------------------------------------------------
# Basic Field Types
# -------------------------------------------------

class PrimitiveField(BaseModel):
    """
    A single path or query parameter. Path/query params only ever allow
    these flat, non-nested types (unlike body fields, which also allow
    `object`/`array`): string, integer, number, boolean.
    """
    type: PrimitiveType
    description: str = Field(..., min_length=1)
    # dynamic_variable: Optional[str] = None

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return _require_non_blank(v, "description")

    model_config = {"extra": "forbid"}


# -------------------------------------------------
# Query Params Schema
# -------------------------------------------------

class QueryParamsSchema(BaseModel):
    """
    `properties` maps each query param name to a `PrimitiveField` (type +
    description) — every query param is optional, so there's no `required`
    list here (unlike `RequestBodySchema`, where a body field can be
    mandatory). Allowed only for the tool's request — never confused with
    path params, which are declared separately via `path_params_schema` and
    must exactly match the `{placeholders}` in `url`.
    """
    properties: Dict[str, PrimitiveField]

    @field_validator("properties")
    @classmethod
    def _validate_property_names(cls, v: Dict[str, PrimitiveField]) -> Dict[str, PrimitiveField]:
        for key in v:
            if not key.strip():
                raise ValueError("query_params_schema.properties cannot contain an empty or whitespace-only key")
        return v

    model_config = {"extra": "forbid"}

# -------------------------------------------------
# Request Body Schema
# -------------------------------------------------

BodyFieldType = Literal["string", "integer", "number", "boolean", "object", "array"]


class BodyField(BaseModel):
    """
    A single JSON body field. Unlike query/path params (restricted to
    `PrimitiveType`: string, integer, number, boolean), body fields also
    allow nested shapes: `type` may additionally be object or array.
      - `object`: describe its members via `properties` (required); `items`
        is not allowed.
      - `array`: describe the element shape via `items` (required);
        `properties` is not allowed.
      - string/integer/number/boolean: neither `properties` nor `items` is
        allowed.
    """
    type: BodyFieldType
    description: str = Field(..., min_length=1)
    items: Optional["BodyField"] = None
    properties: Optional[Dict[str, "BodyField"]] = None
    required: Optional[List[str]] = None

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return _require_non_blank(v, "description")

    @field_validator("properties")
    @classmethod
    def _validate_property_names(cls, v):
        if v:
            for key in v:
                if not key.strip():
                    raise ValueError("properties cannot contain an empty or whitespace-only key")
        return v

    @model_validator(mode="after")
    def _validate_shape(self):
        if self.type == "object":
            if not self.properties:
                raise ValueError("properties is required when type is 'object'")
            if self.items is not None:
                raise ValueError("items is not allowed when type is 'object' (use properties instead)")
            _validate_required_against_properties(self.required, self.properties, "properties")
        elif self.type == "array":
            if self.items is None:
                raise ValueError("items is required when type is 'array'")
            if self.properties is not None:
                raise ValueError("properties is not allowed when type is 'array' (use items instead)")
            if self.required:
                raise ValueError("required is not allowed when type is 'array'")
        else:  # string, integer, number, boolean
            if self.properties is not None:
                raise ValueError(f"properties is not allowed when type is '{self.type}'")
            if self.items is not None:
                raise ValueError(f"items is not allowed when type is '{self.type}'")
            if self.required:
                raise ValueError(f"required is not allowed when type is '{self.type}'")
        return self

    model_config = {"extra": "forbid"}
BodyField.model_rebuild()


class RequestBodySchema(BaseModel):
    """
    JSON request body shape for a POST/PUT/PATCH tool call — always an
    `object` at the top level (there's no `type` field here — it can only
    ever be "object", so it's implied rather than accepted as input), with
    `properties` (name -> `BodyField`) and `required` listing which of those
    names are mandatory (every name must actually exist in `properties`).
    GET/DELETE tools may never carry a request body at all (see `ApiSchema`).
    """
    properties: Dict[str, BodyField] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)

    @field_validator("required", mode="before")
    @classmethod
    def _required_not_null(cls, v):
        if v is None:
            raise ValueError("Required is invalid — must be a list of field names, or omitted")
        return v

    @field_validator("properties")
    @classmethod
    def _validate_property_names(cls, v: Dict[str, BodyField]) -> Dict[str, BodyField]:
        for key in v:
            if not key.strip():
                raise ValueError("properties cannot contain an empty or whitespace-only key")
        return v

    @model_validator(mode="after")
    def _validate_required_keys(self):
        _validate_required_against_properties(self.required, self.properties, "")
        return self

    model_config = {"extra": "forbid"}


def sanitize_stored_body_schema(body_schema: Optional[dict]) -> Optional[dict]:
    """
    Rows written before the top-level `type` field was removed from
    `RequestBodySchema` still have `{"type": "object", "properties": ...}`
    stored in the DB — strip that stray key before reconstructing a
    `RequestBodySchema`/`ApiSchema` from stored data, so reading an
    old/existing function doesn't fail extra="forbid" validation.
    """
    if not body_schema or "type" not in body_schema:
        return body_schema
    sanitized = dict(body_schema)
    sanitized.pop("type", None)
    return sanitized



# -------------------------------------------------
# Main API Schema
# -------------------------------------------------

class ApiSchema(BaseModel):
    """
    Describes the external HTTP API a custom tool calls when an agent
    invokes it.

    Allowed `method` values: GET, POST, PUT, PATCH, DELETE.
      - POST must always carry a `request_body_schema` and `content_type` —
        ElevenLabs rejects a bodiless POST tool.
      - PUT/PATCH may optionally carry a `request_body_schema` (+ matching
        `content_type`, required whenever a body is present).
      - GET/DELETE may never carry a `request_body_schema`, but `content_type`
        is still accepted (just has no effect without a body).
    Any method may use `path_params_schema` (must exactly match every
    `{placeholder}` in `url`) and/or `query_params_schema` — and no property
    name may be reused across path params, query params, and body fields
    (ElevenLabs does not support that).
    `response_variables` maps a variable name to a dot-path into the
    response JSON (e.g. `{"order_status": "status"}`), letting a later step
    of the conversation reference a value the call returned.
    """
    url: str
    method: HttpMethod = Field(
        ...,
        description="Allowed values: GET, POST, PUT, PATCH, DELETE. POST requires request_body_schema + content_type; GET/DELETE cannot carry a request_body_schema.",
    )
    request_headers: Dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_api_url(v)

    @field_validator("request_headers")
    @classmethod
    def _validate_request_headers(cls, v):
        return _validate_headers_dict(v)

    path_params_schema: Optional[Dict[str, PrimitiveField]] = None
    query_params_schema: Optional[QueryParamsSchema] = None
    request_body_schema: Optional[RequestBodySchema] = None
    content_type: Optional[ContentType] = None

    response_variables: Optional[Dict[str, str]] = Field(
        default=None,
        description="Maps a variable name to a dot-path into the API's JSON response, e.g. {\"order_status\": \"status\"}.",
    )

    @field_validator("path_params_schema")
    @classmethod
    def _validate_path_params_names(cls, v):
        if v:
            for key in v:
                if not key.strip():
                    raise ValueError("path_params_schema cannot contain an empty or whitespace-only key")
        return v

    @field_validator("response_variables", mode="before")
    @classmethod
    def _validate_response_variables(cls, v):
        return _validate_response_variables_dict(v)

    model_config = {"extra": "forbid"}

    def _validate_schema_rules(self):
        placeholders = set(re.findall(r"{(.*?)}", self.url))

        # ---------------------------
        # PATH PARAM VALIDATION
        # ---------------------------
        if placeholders:
            if not self.path_params_schema:
                raise ValueError(
                    f"URL contains placeholders {placeholders} but path_params_schema not provided"
                )

            schema_keys = set(self.path_params_schema.keys())

            if schema_keys != placeholders:
                raise ValueError(
                    f"path_params_schema keys {schema_keys} must exactly match URL placeholders {placeholders}"
                )
        else:
            if self.path_params_schema:
                raise ValueError(
                    "path_params_schema provided but URL contains no placeholders"
                )

        # ---------------------------
        # KEY OVERLAP VALIDATION
        # ---------------------------
        path_keys = set(self.path_params_schema.keys()) if self.path_params_schema else set()
        query_keys = set(self.query_params_schema.properties.keys()) if self.query_params_schema else set()
        body_keys = set(self.request_body_schema.properties.keys()) if self.request_body_schema else set()

        path_query_overlap = path_keys & query_keys
        if path_query_overlap:
            raise ValueError(f"Path and query parameter keys cannot be the same: {sorted(path_query_overlap)}")

        path_body_overlap = path_keys & body_keys
        if path_body_overlap:
            raise ValueError(f"Path parameter and body field keys cannot be the same: {sorted(path_body_overlap)}")

        # ElevenLabs does not support a query param and a body field sharing
        # the same name.
        query_body_overlap = query_keys & body_keys
        if query_body_overlap:
            raise ValueError(
                f"Query parameter and body field keys cannot be the same: {sorted(query_body_overlap)}"
            )

        # ---------------------------
        # BODY + CONTENT TYPE VALIDATION
        # ---------------------------
        if self.method == HttpMethod.POST:
            # ElevenLabs rejects POST tools with no request body at
            # tool-creation time — catch it here instead of surfacing that
            # raw 422 to the user.
            if not self.request_body_schema:
                raise ValueError("request_body_schema is required for POST requests")
            if not self.content_type:
                raise ValueError("content_type is required for POST requests")
        elif self.method in {HttpMethod.PUT, HttpMethod.PATCH}:
            if self.request_body_schema and not self.content_type:
                raise ValueError("content_type is required when request_body_schema is provided")
            # content_type without a request_body_schema is allowed here.
        else:  # GET / DELETE
            if self.request_body_schema:
                raise ValueError(f"{self.method} does not allow request_body_schema")
            # content_type alone (with no body) is allowed even for GET/DELETE.

        return self

    # -------------------------------------------------
    # Cross-field validation
    # -------------------------------------------------

    @model_validator(mode="after")
    def validate_schema_rules(self):
        return self._validate_schema_rules()


# -------------------------------------------------
# Wrapper Schemas for API Routes
# -------------------------------------------------

class FunctionCreateSchema(BaseModel):
    """
    Payload for POST /api/v2/public/functions. See
    `PUBLIC_CREATE_FUNCTION_BODY_EXAMPLE` below for one complete, realistic
    request body — with a dummy-but-real-looking URL, an Authorization
    header, and a `response_variables` mapping — plus inline `//` comments
    covering the other allowed HTTP methods and field types.
    """
    name: str = Field(..., min_length=3, description="snake_case: lowercase letters, numbers, and underscores only, starting with a letter (e.g. \"get_weather_forecast\").")
    description: str = Field(..., min_length=10)
    # Using the new ApiSchema for execution config
    api_config: ApiSchema
    # Server-assigned; a caller can never set this — accepted (so sending it
    # isn't a hard error) but always ignored.
    is_system_managed: Optional[bool] = Field(default=None, description="Ignored — this flag is server-assigned and cannot be set by the caller.")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _validate_function_name(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return _require_non_blank(v, "description")

    model_config = {"extra": "forbid"}


# Raw, hand-formatted JSON (with real "//" comments) used ONLY as the request
# body example shown in Swagger/Postman for POST /functions (wired in via
# `openapi_extra` in public_api.py) — same convention as
# PUBLIC_CREATE_WIDGET_BODY_EXAMPLE in widget_schema.py. Kept as a plain
# string, not a dict: dict-shaped `examples` (plural) rendered as generic
# type placeholders ("string", 0, ...) when a Postman collection was
# generated from the OpenAPI spec, whereas this singular, pre-formatted
# `example` string reproduces exactly as written. Anyone sending a real
# request must strip the comment lines first, since they're not valid JSON.
PUBLIC_CREATE_FUNCTION_BODY_EXAMPLE = """{
  "name": "book_appointment",
  "description": "Books a new appointment for the caller with the given date, time, and reason.",
  "api_config": {
    // method: GET, POST, PUT, PATCH, or DELETE.
    // - POST always requires request_body_schema + content_type.
    // - PUT/PATCH may optionally have request_body_schema + content_type.
    // - GET/DELETE can never have a request_body_schema (content_type is
    //   still accepted for them, it just has no effect without a body).
    // url must be https:// — http:// and localhost/private addresses are rejected.
    "url": "https://api.mybusiness.com/v1/appointments",
    "method": "POST",
    // Authorization is just a common example header name — replace the
    // value below with whatever your own API actually expects (Bearer
    // token, API key, Basic auth, etc). Keys and values must be non-empty.
    "request_headers": {
      "Authorization": "Bearer YOUR_API_ACCESS_TOKEN"
    },
    //accepted values of content type are: application/json and application/x-www-form-urlencoded
    "content_type": "application/json",
    // query_params_schema / path_params_schema fields allow ONLY these 4
    // types: string, integer, number, boolean — no object/array, since a
    // URL query string/path segment can't represent a nested shape.
    // every query param is optional (no "required" list) — an agent may or
    // may not supply it depending on the conversation. A query/path param
    // name can never also be used as a body field name below.
    "query_params_schema": {
      "properties": {
        "send_confirmation": {"type": "boolean", "description": "Whether to email the caller a confirmation once booked"}
      }
    },
    // request_body_schema is implicitly an object — there's no "type" key
    // here (only inside each property). Its fields allow string/integer/
    // number/boolean plus nested shapes: object (describe its members via
    // "properties", like "attendee" below) and array (describe the element
    // shape via "items", like "guest_names" below). Every field needs both
    // "type" and a non-empty "description".
    "request_body_schema": {
      "properties": {
        "customer_name": {"type": "string", "description": "Full name of the customer booking the appointment"},
        "appointment_date": {"type": "string", "description": "Appointment date in YYYY-MM-DD format"},
        "appointment_time": {"type": "string", "description": "Appointment time in HH:MM (24h) format"},
        "reason": {"type": "string", "description": "Reason for the appointment"},
        "attendee": {
          "type": "object",
          "description": "The primary attendee's contact details",
          "properties": {
            "email": {"type": "string", "description": "Attendee's email address"},
            "phone": {"type": "string", "description": "Attendee's phone number"}
          }
        },
        "guest_names": {
          "type": "array",
          "description": "Names of any additional guests joining the appointment",
          "items": {"type": "string", "description": "A single guest's full name"}
        }
      },
      // every name here must exist as a key in "properties" above.
      "required": ["customer_name", "appointment_date", "appointment_time"]
    },
    // response_variables maps a variable name to a dot-path into the API's
    // JSON response, so a later step of the conversation can read back a
    // value the call returned — names below are just examples of that shape.
    "response_variables": {
      "created_user_id": "customer.id",
      "user_new_appointments": "customer.appointments_count"
    }
  }
}"""


class ApiUpdateSchema(BaseModel):
    url: str
    method: Optional[HttpMethod] = None
    request_headers: Optional[Dict[str, str]] = None
    path_params_schema: Optional[Dict[str, PrimitiveField]] = None
    query_params_schema: Optional[QueryParamsSchema] = None
    request_body_schema: Optional[RequestBodySchema] = None
    content_type: Optional[ContentType] = None
    response_variables: Optional[Dict[str, str]] = None

    model_config = {"extra": "forbid"}

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_api_url(v)

    @field_validator("request_headers")
    @classmethod
    def _validate_request_headers(cls, v):
        return _validate_headers_dict(v)

    @field_validator("path_params_schema")
    @classmethod
    def _validate_path_params_names(cls, v):
        if v:
            for key in v:
                if not key.strip():
                    raise ValueError("path_params_schema cannot contain an empty or whitespace-only key")
        return v

    @field_validator("response_variables", mode="before")
    @classmethod
    def _validate_response_variables(cls, v):
        return _validate_response_variables_dict(v)

    @model_validator(mode="after")
    def validate_schema_rules(self):
        # We can reuse the logic by temporarily creating an ApiSchema if needed,
        # but better to just share the method logic.
        # Since self has same attributes as ApiSchema (or compatible), it works.
        return ApiSchema._validate_schema_rules(self)


class FunctionUpdateSchema(BaseModel):
    """
    Payload for PUT /api/v2/public/functions/{id} — a true PUT (name,
    description, and api_config are all required and replace the tool
    wholesale). See `PUBLIC_UPDATE_FUNCTION_BODY_EXAMPLE` below for a
    complete, realistic request body. `is_system_managed` is a
    server-assigned flag, not something a caller can set — it's deliberately
    NOT a declared field here, so `extra = "forbid"` below rejects any
    payload that includes it.
    """
    name: str = Field(..., min_length=3, description="snake_case: lowercase letters, numbers, and underscores only, starting with a letter (e.g. \"get_weather_forecast\").")
    description: str = Field(..., min_length=10)
    api_config: ApiUpdateSchema
    response_variables: Optional[Dict[str, str]] = None # Allow top-level update too

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _validate_function_name(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return _require_non_blank(v, "description")

    @field_validator("response_variables", mode="before")
    @classmethod
    def _validate_response_variables(cls, v):
        return _validate_response_variables_dict(v)

    model_config = {"extra": "forbid"}


# Same convention/rationale as PUBLIC_CREATE_FUNCTION_BODY_EXAMPLE above —
# raw JSON string (not a dict) so Swagger/Postman render these exact,
# realistic values instead of generic type placeholders.
PUBLIC_UPDATE_FUNCTION_BODY_EXAMPLE = """{
  "name": "book_appointment",
  "description": "Books a new appointment for the caller with the given date, time, and reason — now also supports marking a booking as a reschedule.",
  "api_config": {
    // Same method + field-type rules as POST /functions — see its example.
    "url": "https://api.mybusiness.com/v1/appointments",
    "method": "POST",
    "request_headers": {
      "Authorization": "Bearer YOUR_API_ACCESS_TOKEN"
    },
    //accepted values of content type are: application/json and application/x-www-form-urlencoded
    "content_type": "application/json",
    "query_params_schema": {
      "properties": {
        "send_confirmation": {"type": "boolean", "description": "Whether to email the caller a confirmation once booked"},
        "reschedule": {"type": "boolean", "description": "Whether this booking replaces an existing appointment"}
      }
    },
    // request_body_schema is implicitly an object — no "type" key here
    // (only inside each property). Fields allow string/integer/number/
    // boolean plus object ("attendee" below, via "properties") and array
    // ("guest_names" below, via "items") — query/path params above cannot
    // use those last two, and no name here may repeat a query/path name.
    "request_body_schema": {
      "properties": {
        "customer_name": {"type": "string", "description": "Full name of the customer booking the appointment"},
        "appointment_date": {"type": "string", "description": "Appointment date in YYYY-MM-DD format"},
        "appointment_time": {"type": "string", "description": "Appointment time in HH:MM (24h) format"},
        "reason": {"type": "string", "description": "Reason for the appointment"},
        "attendee": {
          "type": "object",
          "description": "The primary attendee's contact details",
          "properties": {
            "email": {"type": "string", "description": "Attendee's email address"},
            "phone": {"type": "string", "description": "Attendee's phone number"}
          }
        },
        "guest_names": {
          "type": "array",
          "description": "Names of any additional guests joining the appointment",
          "items": {"type": "string", "description": "A single guest's full name"}
        }
      },
      "required": ["customer_name", "appointment_date", "appointment_time"]
    }
  },
  // response_variables may also be set at this top level instead of (or in
  // addition to) inside api_config — both are supported.
  "response_variables": {
    "created_user_id": "customer.id",
    "user_new_appointments": "customer.appointments_count"
  }
}"""


class FunctionRead(BaseModel):
    """
    Full tool shape returned to the DASHBOARD (internal) API — includes
    `elevenlabs_tool_id`. The public API uses `PublicFunctionRead` (below)
    instead, which omits it.
    """
    id: int
    name: str
    description: str
    api_config: Optional[ApiSchema] = None
    elevenlabs_tool_id: Optional[str] = None
    created_at: datetime
    modified_at: datetime
    # Number of the current user's agents this tool is attached to.
    agents_count: int = 0
    # True for the auto-provisioned search_personal_knowledge_base tool — the
    # frontend uses this to hide edit/delete and show name-only, since these
    # tools are managed automatically and can't be edited/viewed in detail.
    is_system_managed: bool = False

    model_config = {"from_attributes": True}


class PublicFunctionRead(BaseModel):
    """
    Full tool shape returned by the public API's single-item endpoints (GET
    /functions/{id}, POST /functions, PUT /functions/{id}) — same as
    FunctionRead but omits `elevenlabs_tool_id`, an internal id never useful
    to a public caller.
    """
    id: int
    name: str
    description: str
    api_config: Optional[ApiSchema] = None
    created_at: datetime
    modified_at: datetime
    agents_count: int = 0
    is_system_managed: bool = False

    model_config = {"from_attributes": True}


class PublicFunctionListRead(BaseModel):
    """
    Trimmed-down item shape for GET /api/v2/public/functions (list only) —
    unlike PublicFunctionRead (used by the single-item GET/POST/PUT), this
    omits `api_config` (may contain secrets — fetch a specific tool by id to
    see its config) and `description`, keeping only `method` as a
    lightweight summary of the tool's config.
    """
    id: int
    name: str
    method: Optional[HttpMethod] = None
    created_at: datetime
    modified_at: datetime
    agents_count: int = 0
    is_system_managed: bool = False

    model_config = {"from_attributes": True}

class FunctionAgentItem(BaseModel):
    id: int
    agent_name: str

class FunctionBind(BaseModel):
    agent_id: int
    function_id: int

class FunctionUnbind(BaseModel):
    agent_id: int
    function_id: int

    @model_validator(mode="before")
    @classmethod
    def map_database_fields(cls, data: Any) -> Any:
        # If data is a SQLAlchemy model (FunctionModel)
        if hasattr(data, "api_endpoint_url") and data.api_endpoint_url:
            db_config = data.api_endpoint_url
            # Decrypt auth-related headers
            headers = db_config.headers or {}
            sensitive_keys = {"authorization", "x-api-key", "api-key", "token"}
            decrypted_headers = {}
            for k, v in headers.items():
                if k.lower() in sensitive_keys:
                    try:
                        decrypted_headers[k] = decrypt_data(v)
                    except Exception:
                        decrypted_headers[k] = v # Fallback if not encrypted or key issue
                else:
                    decrypted_headers[k] = v

            # Pre-populate api_config for the model_validate call
            api_config_data = {
                "url": db_config.endpoint_url,
                "method": db_config.http_method,
                "request_headers": decrypted_headers,
                "path_params_schema": {k: PrimitiveField(**v) for k, v in db_config.path_params.items()} if db_config.path_params else None,
                "query_params_schema": db_config.query_params if db_config.query_params else None,
                "request_body_schema": sanitize_stored_body_schema(db_config.body_schema),
                "response_variables": db_config.response_variables if db_config.response_variables else None,
                "content_type": "application/json" if db_config.body_schema else None,
            }

            # Create a dict that Pydantic can use to populate FunctionRead
            return {
                "id": data.id,
                "name": data.name,
                "description": data.description,
                "api_config": api_config_data,
                "elevenlabs_tool_id": data.elevenlabs_tool_id,
                "created_at": data.created_at,
                "modified_at": data.modified_at
            }
        return data
