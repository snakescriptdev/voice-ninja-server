from pydantic import BaseModel, Field
from typing import List, Optional

class AgentCreate(BaseModel):
    agent_name: str
    first_message: str | None = None
    system_prompt: str

    voice: str                  # voice_name
    ai_models: str       # model_name list
    languages: str = Field(description="language code to be passed in model (en-01 for english)")




class AgentUpdate(BaseModel):
    agent_name: Optional[str] = None
    first_message: Optional[str] = None
    system_prompt: Optional[str] = None
    voice: Optional[str] = None
    ai_models: Optional[str] = None
    languages: Optional[str] = Field(default=None,description="language code to be passed in model (en-01 for english)")

class AgentRead(BaseModel):
    id: int
    agent_name: str
    first_message: str | None
    system_prompt: str
    voice:str

    class Config:
        from_attributes = True