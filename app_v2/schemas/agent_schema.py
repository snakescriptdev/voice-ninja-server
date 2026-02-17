from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime

class AgentCreate(BaseModel):
    agent_name: str
    first_message: str | None = None
    system_prompt: str
    phone: Optional[str] = Field(None, description="Phone number to assign to this agent (e.g., +14155551234)")
    voice: str                 
    ai_model: str       
    language: str = Field(description="language code to be passed in model (en-01 for english)")
    knowledgebase: Optional[List[int | Dict]] = Field(default=[], description="List of knowledge base IDs or objects")
    variables: Optional[Dict[str, str]] = Field(default={}, description="Dynamic variables for the agent")
    tools: Optional[List[int | Dict]] = Field(default=[], description="List of function/tool IDs or objects")




class AgentUpdate(BaseModel):
    agent_name: Optional[str] = None
    first_message: Optional[str] = None
    system_prompt: Optional[str] = None
    voice: Optional[str] = None
    ai_model: Optional[str] = None
    language: Optional[str] = Field(default=None,description="language code to be passed in model (en-01 for english)")
    phone: Optional[str] = Field(None, description="Phone number to assign to this agent (e.g., +14155551234)")
    knowledgebase: Optional[List[int | Dict]] = None
    variables: Optional[Dict[str, str]] = None
    tools: Optional[List[int | Dict]] = None


class AgentRead(BaseModel):
    id: int
    agent_name: str
    first_message: str | None
    system_prompt: str
    voice:str
    updated_at: datetime
    phone: Optional[str] = None
    ai_model: str
    language: str
    elevenlabs_agent_id: Optional[str] = None
    knowledgebase: List[dict[str,int|str]] = []
    variables: Dict[str, str] = {}
    tools: List[dict[str,int|str]] = []
    class Config:
        from_attributes = True