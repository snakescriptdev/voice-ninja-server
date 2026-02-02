from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime



class VariableBaseSchema(BaseModel):
    variable_name: str = Field(..., example="user_name")
    variable_value: str = Field(..., example="Vikram")


class VariableCreateSchema(VariableBaseSchema):
    pass



class VariableUpdateSchema(BaseModel):
    variable_name: Optional[str] = None
    variable_value: Optional[str] = None





class VariableReadSchema(VariableBaseSchema):
    id: int
    agent_id: int
    created_at: datetime
    modified_at: datetime

    class Config:
        from_attributes = True   # SQLAlchemy 2.0 (replaces orm_mode)
