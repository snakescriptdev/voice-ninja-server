from pydantic import BaseModel, Field 
from typing import List
from app_v2.schemas.enum_types import UseCases, Capebilites,ResponseStyleEnum




class AgentConfigGenerator(BaseModel):
    #base settings
    agent_name: str
    language: str
    main_goal: str

    #use cases
    use_cases: List[UseCases] = Field(...,min_length=1,max_length=6,description="list of use cases for agent") #max length can change in future

    #config
    voice: str
    ai_model: str
    response_style: ResponseStyleEnum

    #capabilites
    capebilites: List[Capebilites] = Field(...,min_length=1,max_length=4,description="list of capabilites of agent") # max lenght can change in fututre



class AgentConfigOut(BaseModel):
    agent_name: str
    ai_model: str 
    voice: str
    language: str
    system_prompt: str

