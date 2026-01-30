from pydantic import BaseModel, Field 
from typing import List, Optional
from enum import Enum


class UseCases(str,Enum):
    email_assistant = "email_assistant"
    task_execution = "task_execution"
    system_assistant = "system_assistant"
    knowledge_lookup = "knowledge_lookup"
    customer_support = "customer_support"
    custom = "custom"


class Capabilites(str,Enum):
    email_integration = "email_integration"
    calendar_management = "calendar_management"
    knowledge_base = "knowledge_base"
    api_integration = "api_integration"


class ResponseStyleEnum(str, Enum):
    professional = "professional"
    friendly = "friendly"
    casual = "casual"



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
    capabilites: List[Capabilites] = Field(...,min_length=1,max_length=4,description="list of capabilites of agent") # max lenght can change in fututre



class AgentConfigOut(BaseModel):
    agent_name: str
    ai_model: str 
    voice: str
    language: str
    system_prompt: str

