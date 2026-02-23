from pydantic import BaseModel
from typing import Optional

class UserCostItem(BaseModel):
    user_id: int
    user_name: str
    email: str
    total_cost: float

    model_config = {"from_attributes": True}
