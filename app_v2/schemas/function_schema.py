from pydantic import BaseModel, Field, AnyHttpUrl, field_validator
from typing import Optional, Dict, List, Any
from datetime import datetime

from app_v2.schemas.enum_types import RequestMethodEnum


# -------------------------------------------------------------------
# Base Function (LLM-facing semantic definition)
# -------------------------------------------------------------------

class BaseFunctionSchema(BaseModel):
    """
    Semantic definition of a function (LLM-facing)
    """
    name: str = Field(
        ...,
        min_length=3,
        description="Unique name of the function"
    )
    description: str = Field(
        ...,
        min_length=10,
        description="What this function does"
    )


# -------------------------------------------------------------------
# Function API Execution Config
# -------------------------------------------------------------------

class FunctionApiConfigSchema(BaseModel):
    """
    Execution configuration for a function.
    One function can have multiple API configs.
    """
    endpoint_url: AnyHttpUrl = Field(..., description="HTTP endpoint to invoke")
    http_method: RequestMethodEnum
    timeout_ms: Optional[int] = Field(default=20000, gt=0)

    headers: Dict[str, Any] = Field(default_factory=dict)
    query_params: Dict[str, Any] = Field(default_factory=dict)

    llm_response_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="Expected schema of LLM-parsed response"
    )
    response_variables: Dict[str, Any] = Field(
        default_factory=dict,
        description="Variables to extract from API response"
    )

    @field_validator("endpoint_url")
    @classmethod
    def forbid_query_string(cls, v):
        if v.query:
            raise ValueError("Query params must be defined in query_params field")
        return v


# -------------------------------------------------------------------
# Agent-specific Function Behavior
# -------------------------------------------------------------------

class AgentFunctionConfigSchema(BaseModel):
    """
    Agent-specific behavior for a function
    """
    speak_while_execution: bool = Field(default=False)
    speak_after_execution: bool = Field(default=True)


# -------------------------------------------------------------------
# Create / Update Schemas
# -------------------------------------------------------------------

class FunctionCreateSchema(BaseFunctionSchema):
    """
    Create function + its API configs + optional agent behavior
    """
    api_configs: List[FunctionApiConfigSchema] = Field(
        ...,
        min_items=1,
        description="At least one execution config is required"
    )
    agent_config: Optional[AgentFunctionConfigSchema] = None
    agent_id: Optional[int] = None


class FunctionUpdateSchema(BaseModel):
    """
    Partial update for function and its configs
    """
    name: Optional[str] = None
    description: Optional[str] = None
    api_configs: Optional[List[FunctionApiConfigSchema]] = None
    agent_config: Optional[AgentFunctionConfigSchema] = None


# -------------------------------------------------------------------
# Read Schemas
# -------------------------------------------------------------------

class FunctionApiConfigRead(FunctionApiConfigSchema):
    id: int
    created_at: datetime
    modified_at: datetime

    model_config = {"from_attributes": True}


class FunctionRead(BaseFunctionSchema):
    id: int
    api_configs: List[FunctionApiConfigRead]
    created_at: datetime
    modified_at: datetime
    agent_config: Optional[AgentFunctionConfigSchema] = None

    model_config = {"from_attributes": True}


class AgentFunctionRead(BaseModel):
    id: int
    speak_while_execution: bool
    speak_after_execution: bool
    function: FunctionRead
    created_at: datetime
    modified_at: datetime

    model_config = {"from_attributes": True}
