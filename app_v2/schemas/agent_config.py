from pydantic import BaseModel, Field, field_validator
from typing import List
from app_v2.schemas.enum_types import UseCases, Capebilites,ResponseStyleEnum
from typing import Optional


class AgentConfigGenerator(BaseModel):
    #base settings
    agent_name: str
    language: str
    main_goal: Optional[str] = Field(description="main goal of agent",max_length=500)

    #use cases
    use_cases: Optional[List[UseCases]] = Field(None,min_length=1,max_length=6,description="list of use cases for agent") #max length can change in future

    #config
    voice: str
    ai_model: str
    response_style: ResponseStyleEnum

    #capabilites
    capebilites: Optional[List[Capebilites]] = Field(None,min_length=1,max_length=4,description="list of capabilites of agent") # max lenght can change in fututre

    # field validation on mail goal if it is non empty min chars to be 10 and max chars to be 500
    @field_validator('main_goal')
    @classmethod
    def validate_main_goal(cls, v: str) -> str:
        if v is not None and v.strip():
            v = v.strip()
            if len(v) < 10:
                raise ValueError('Main goal must be at least 10 characters long')
            return v
        return None if not v or not v.strip() else v



class AgentConfigOut(BaseModel):
    agent_name: str
    ai_model: str 
    voice: str
    language: str
    system_prompt: str

