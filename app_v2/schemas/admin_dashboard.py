from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app_v2.schemas.pagination import PaginatedResponse
class UserCostItem(BaseModel):
    user_id: int
    user_name: str
    email: str
    total_cost: float

    model_config = {"from_attributes": True}

class AdminConversationItem(BaseModel):
    id: int
    created_at: datetime
    user_id: int
    user_name: str
    user_email: str
    agent_name: Optional[str] = None
    channel: Optional[str] = None
    call_status: Optional[str] = None
    duration: Optional[int] = None
    elevenlabs_cost: float
    coins_deducted: int

    model_config = {"from_attributes": True}
