
from pydantic import BaseModel, Field, field_validator

from app_v2.schemas.agent_schema import AgentRead
from app_v2.schemas.enum_types import AgentBuildStatusEnum

# Shared with the multipart form handler in app_v2/routers/agent_build.py,
# which can't run this Pydantic model directly (Form/File params aren't a
# JSON body) but still needs the exact same rules applied by hand.
MIN_REQUIREMENT_LENGTH = 10
MAX_REQUIREMENT_LENGTH = 2000
MAX_KNOWLEDGE_URLS = 5
MAX_KNOWLEDGE_FILES = 5


def validate_requirement_text(v: str) -> str:
    v = (v or "").strip()
    if len(v) < MIN_REQUIREMENT_LENGTH:
        raise ValueError("Please describe your agent in at least 10 characters")
    if len(v) > MAX_REQUIREMENT_LENGTH:
        raise ValueError("Description must be 2000 characters or fewer")
    return v


class AgentBuildRequest(BaseModel):
    requirement: str = Field(..., description="Freeform description of the agent to build")

    @field_validator("requirement")
    @classmethod
    def validate_requirement(cls, v: str) -> str:
        return validate_requirement_text(v)


class AgentBuildJobOut(BaseModel):
    id: int
    status: AgentBuildStatusEnum
    error_message: str | None = None
    agent: AgentRead | None = None
