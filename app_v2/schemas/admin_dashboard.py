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

class UserDetailItem(BaseModel):
    user_id: int
    username: str
    email: str
    plan: Optional[str] = "No Plan"
    coins_count: int = 0
    agents_count: int = 0
    phones_count: int = 0
    last_active: Optional[datetime] = None

class PlanUserCount(BaseModel):
    plan_name: str
    count: int

class UserCountOverviewResponse(BaseModel):
    status: str
    total_users: int
    users_by_plan: List[PlanUserCount]
    users: PaginatedResponse[UserDetailItem]

class CoinBundleCreate(BaseModel):
    name: str = Field(...,max_length=90,min_length=3)
    coins: int = Field(...,gt=0)
    price: float = Field(...,gt=0)
    currency: Optional[str] = "INR"
    validity_days: Optional[int] = None

class CoinBundleResponse(BaseModel):
    id: int
    name: str
    coins: int
    price: float
    currency: str
    is_active: bool
    validity_days: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}
