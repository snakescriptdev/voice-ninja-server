from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional

from app_v2.schemas.enum_types import PhoneNumberAssignStatus


class TwilioConnectorCreate(BaseModel):
    name: str
    account_sid: str
    auth_token: str

    @field_validator("name", "account_sid", "auth_token")
    @classmethod
    def not_blank(cls, v: str, info) -> str:
        if v is None or not v.strip():
            raise ValueError(f"{info.field_name.replace('_', ' ')} cannot be empty")
        return v.strip()


class TwilioConnectorUpdate(BaseModel):
    name: Optional[str] = None
    account_sid: Optional[str] = None
    auth_token: Optional[str] = None

    @field_validator("name", "account_sid", "auth_token")
    @classmethod
    def not_blank(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return v
        if not v.strip():
            raise ValueError(f"{info.field_name.replace('_', ' ')} cannot be empty")
        return v.strip()


class TwilioConnectorResponse(BaseModel):
    id: int
    name: str
    account_sid: str
    auth_token: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConnectorAgentResponse(BaseModel):
    agent_id: int
    agent_name: str
    phone_number: str
    status: PhoneNumberAssignStatus

    class Config:
        from_attributes = True
