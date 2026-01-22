from pydantic import  BaseModel, Field
from typing import  Optional



class AgentRequestSchema(BaseModel):
    '''
    schema class for Agent 
    Attributes:
                -user_id : int
                -agent_name: string
                -system_prompt: string
                -first_message

    '''


    user_id :int = Field(...,description="id of user who creats agent")
    agent_name:str = Field(...,min_length=3,max_length=50,description="name of the agent")
    system_prompt:str = Field(...,description="defines the role and responsibiltes of user",min_length=50)
    first_message: str = Field(description="the message agent usesto greet")


class AgentResponseModel(BaseModel):
    agent_id: int
    agent_name: str 


