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
# Basic Field Types
# -------------------------------------------------

class PrimitiveField(BaseModel):
    """
    A single path or query parameter. Path/query params only ever allow
    these flat, non-nested types (unlike body fields, which also allow
    `object`/`array`): string, integer, number, boolean.
    """
    type: PrimitiveType
    description: str
    # dynamic_variable: Optional[str] = None

    model_config = {"extra": "ignore"}


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

    # "ignore" (not "forbid"): a caller/old stored row that still sends a
    # `required` key here — from before this field was removed — is
    # silently dropped rather than rejected.
    model_config = {"extra": "ignore"}

# -------------------------------------------------
# Request Body Schema
# -------------------------------------------------

BodyFieldType = Literal["string", "integer", "number", "boolean", "object", "array"]


class BodyField(BaseModel):
    """
    A single JSON body field. Unlike query/path params (restricted to
    `PrimitiveType`: string, integer, number, boolean), body fields also
    allow nested shapes: `type` may additionally be object or array — for
    `object`, describe its members via `properties`; for `array`, describe
    the element shape via `items`.
    """
    type: Optional[BodyFieldType] = None
    description: Optional[str] = None
    items: Optional["BodyField"] = None
    properties: Optional[Dict[str, "BodyField"]] = None
    required: Optional[List[str]] = None

    model_config = {"extra": "ignore"}
BodyField.model_rebuild()


class RequestBodySchema(BaseModel):
    """
    JSON request body shape for a POST/PUT/PATCH tool call — always an
    `object` at the top level, with `properties` (name -> `BodyField`) and
    `required` listing which of those names are mandatory. GET/DELETE tools
    may never carry a request body at all (see `ApiSchema`).
    """
    type: Literal["object"]
    properties: Dict[str, BodyField] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}



# -------------------------------------------------
# Main API Schema
# -------------------------------------------------

class ApiSchema(BaseModel):
    """
    Describes the external HTTP API a custom tool calls when an agent
    invokes it.

    Allowed `method` values: GET, POST, PUT, PATCH, DELETE.
      - POST must always carry a `request_body_schema` (+ matching
        `content_type`) — ElevenLabs rejects a bodiless POST tool.
      - PUT/PATCH may optionally carry a `request_body_schema` the same way.
      - GET/DELETE may never carry a `request_body_schema` or `content_type`
        — attach parameters via `query_params_schema`/`path_params_schema`
        instead.
    Any method may use `path_params_schema` (must exactly match every
    `{placeholder}` in `url`) and/or `query_params_schema`, and path/query
    param names may never collide.
    `response_variables` maps a variable name to a dot-path into the
    response JSON (e.g. `{"order_status": "status"}`), letting a later step
    of the conversation reference a value the call returned.
    """
    url: str
    method: HttpMethod = Field(
        ...,
        description="Allowed values: GET, POST, PUT, PATCH, DELETE. POST requires request_body_schema + content_type; GET/DELETE cannot carry either.",
    )
    request_headers: Dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        try:
            parsed = urlparse(v)
            if not parsed.scheme or parsed.scheme not in ("http", "https"):
                raise ValueError("URL must start with http:// or https://")
            if not parsed.netloc:
                raise ValueError("URL must have a valid domain")
            return v
        except Exception:
            raise ValueError("Invalid URL format")

    path_params_schema: Optional[Dict[str, PrimitiveField]] = None
    query_params_schema: Optional[QueryParamsSchema] = None
    request_body_schema: Optional[RequestBodySchema] = None
    content_type: Optional[ContentType] = None

    response_variables: Optional[Dict[str, str]] = Field(
        default=None,
        description="Maps a variable name to a dot-path into the API's JSON response, e.g. {\"order_status\": \"status\"}.",
    )

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
        if self.path_params_schema and self.query_params_schema:
            path_keys = set(self.path_params_schema.keys())
            query_keys = set(self.query_params_schema.properties.keys())
            overlap = path_keys.intersection(query_keys)
            if overlap:
                raise ValueError(f"Path and query parameter keys cannot be same: {overlap}")

        # ---------------------------
        # BODY + CONTENT TYPE VALIDATION
        # ---------------------------
        if self.method in {HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH}:

            if self.request_body_schema:
                if not self.content_type:
                    raise ValueError("content_type is required when request_body_schema is provided")
            else:
                if self.content_type:
                    raise ValueError("content_type cannot be set without request_body_schema")
                # ElevenLabs rejects POST tools with no request body at tool-creation
                # time — catch it here instead of surfacing that raw 422 to the user.
                if self.method == HttpMethod.POST:
                    raise ValueError("request_body_schema is required for POST requests")

        else:  # GET / DELETE
            if self.request_body_schema:
                raise ValueError(f"{self.method} does not allow request_body_schema")
            if self.content_type:
                raise ValueError(f"{self.method} does not allow content_type")

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
    name: str = Field(..., min_length=3)
    description: str = Field(..., min_length=10)
    # Using the new ApiSchema for execution config
    api_config: ApiSchema


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
    // - GET/DELETE can never have a request_body_schema or content_type —
    //   use query_params_schema / path_params_schema for their inputs instead.
    "url": "https://api.mybusiness.com/v1/appointments",
    "method": "POST",
    // Authorization is just a common example header name — replace the
    // value below with whatever your own API actually expects (Bearer
    // token, API key, Basic auth, etc).
    "request_headers": {
      "Authorization": "Bearer YOUR_API_ACCESS_TOKEN"
    },
    //accepted values of content type are: application/json and application/x-www-form-urlencoded
    "content_type": "application/json",
    // query_params_schema / path_params_schema fields allow ONLY these 4
    // types: string, integer, number, boolean — no object/array, since a
    // URL query string/path segment can't represent a nested shape.
    // every query param is optional (no "required" list) — an agent may or
    // may not supply it depending on the conversation.
    "query_params_schema": {
      "properties": {
        "send_confirmation": {"type": "boolean", "description": "Whether to email the caller a confirmation once booked"}
      }
    },
    // request_body_schema fields allow those same 4 types PLUS 2 more,
    // since a JSON body can hold nested shapes: object (describe its
    // members via "properties", like "attendee" below) and array
    // (describe the element shape via "items", like "guest_names" below).
    "request_body_schema": {
      "type": "object",
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
        return ApiSchema.validate_url(v)

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
    complete, realistic request body. `is_system_managed` is deliberately
    not a field here: it's a server-assigned flag, not something a caller
    can set, and any such key sent in the request body is silently dropped
    rather than applied.
    """
    name: str
    description: str = Field(..., min_length=10)
    api_config: ApiUpdateSchema
    response_variables: Optional[Dict[str, str]] = None # Allow top-level update too

    model_config = {"extra": "ignore"}


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
    // request_body_schema fields allow string/integer/number/boolean plus
    // object ("attendee" below, via "properties") and array ("guest_names"
    // below, via "items") — query/path params above cannot use those last two.
    "request_body_schema": {
      "type": "object",
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
                "request_body_schema": db_config.body_schema if db_config.body_schema else None,
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
