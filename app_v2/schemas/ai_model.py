from pydantic import BaseModel,Field
from typing import Optional


class AIModelBaseSchema(BaseModel):
    provider:str = Field(...,description="name of llm provide (like openAI,Cohere)",min_length=3)
    model_name: str = Field(...,description="name of the llm model (gpt-4o,gpt-3.5-turbo)",min_length=3)


class AIModelIn(AIModelBaseSchema):
    pass

class AIModelUpdate(BaseModel):
    provider: Optional[str] = None
    model_name: Optional[str] = None


class AIModelRead(AIModelBaseSchema):
    id: int

    class Config:
        orm_mode = True