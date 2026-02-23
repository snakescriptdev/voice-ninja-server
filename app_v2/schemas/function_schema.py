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
    type: PrimitiveType
    description: Optional[str] = None
    # dynamic_variable: Optional[str] = None
    
    model_config = {"extra": "ignore"}


# -------------------------------------------------
# Query Params Schema
# -------------------------------------------------

class QueryParamsSchema(BaseModel):
    properties: Dict[str, PrimitiveField]
    required: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_required_keys(self):
        for key in self.required:
            if key not in self.properties:
                raise ValueError(f"Query param '{key}' not defined in properties")
        return self

# -------------------------------------------------
# Request Body Schema
# -------------------------------------------------

class BodyField(BaseModel):
    type: Optional[str] = None
    description: Optional[str] = None
    items: Optional["BodyField"] = None
    properties: Optional[Dict[str, "BodyField"]] = None
    required: Optional[List[str]] = None
    
    model_config = {"extra": "ignore"}
BodyField.model_rebuild()


class RequestBodySchema(BaseModel):
    type: Literal["object"]
    properties: Dict[str, BodyField] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}



# -------------------------------------------------
# Main API Schema
# -------------------------------------------------

class ApiSchema(BaseModel):
    url: str
    method: HttpMethod
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
    
    response_variables: Optional[Dict[str, str]] = None

    model_config = {"extra": "forbid"}

    # -------------------------------------------------
    # Cross-field validation
    # -------------------------------------------------

    @model_validator(mode="after")
    def validate_schema_rules(self):
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
        # BODY + CONTENT TYPE VALIDATION
        # ---------------------------
        if self.method in {HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH}:

            if self.request_body_schema:
                if not self.content_type:
                    raise ValueError("content_type is required when request_body_schema is provided")
            else:
                if self.content_type:
                    raise ValueError("content_type cannot be set without request_body_schema")

        else:  # GET / DELETE
            if self.request_body_schema:
                raise ValueError(f"{self.method} does not allow request_body_schema")
            if self.content_type:
                raise ValueError(f"{self.method} does not allow content_type")

        return self


# -------------------------------------------------
# Wrapper Schemas for API Routes
# -------------------------------------------------

class FunctionCreateSchema(BaseModel):
    name: str = Field(..., min_length=3)
    description: str = Field(..., min_length=10)
    # Using the new ApiSchema for execution config
    api_config: ApiSchema


class ApiUpdateSchema(BaseModel):
    url: Optional[str] = None
    method: Optional[HttpMethod] = None
    request_headers: Optional[Dict[str, str]] = None
    path_params_schema: Optional[Dict[str, PrimitiveField]] = None
    query_params_schema: Optional[QueryParamsSchema] = None
    request_body_schema: Optional[RequestBodySchema] = None
    content_type: Optional[ContentType] = None
    response_variables: Optional[Dict[str, str]] = None

    model_config = {"extra": "forbid"}


class FunctionUpdateSchema(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = Field(None, min_length=10)
    api_config: Optional[ApiUpdateSchema] = None
    response_variables: Optional[Dict[str, str]] = None # Allow top-level update too


class FunctionRead(BaseModel):
    id: int
    name: str
    description: str
    api_config: ApiSchema 
    elevenlabs_tool_id: Optional[str] = None
    created_at: datetime
    modified_at: datetime

    model_config = {"from_attributes": True}

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
