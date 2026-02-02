from pydantic import BaseModel, Field,field_validator
from typing import List, Optional
from datetime import datetime
from app_v2.utils.otp_utils import is_phone

class AgentCreate(BaseModel):
    agent_name: str
    first_message: str | None = None
    system_prompt: str
    phone:str 
    voice: str                  # voice_name
    ai_models: str       # model_name list
    languages: str = Field(description="language code to be passed in model (en-01 for english)")

    @field_validator("phone")
    @classmethod
    def phone_validator(cls,v):
        if not is_phone(v):
            raise ValueError("Invalid phone number")
        return v
    




class AgentUpdate(BaseModel):
    agent_name: Optional[str] = None
    first_message: Optional[str] = None
    system_prompt: Optional[str] = None
    voice: Optional[str] = None
    ai_models: Optional[str] = None
    languages: Optional[str] = Field(default=None,description="language code to be passed in model (en-01 for english)")
    phone: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def phone_validator(cls,v):
        if not is_phone(v):
            raise ValueError("Invalid phone number")
        return v


class AgentRead(BaseModel):
    id: int
    agent_name: str
    first_message: str | None
    system_prompt: str
    voice:str
    updated_at: datetime
    phone: Optional[str] = None

    class Config:
        from_attributes = True