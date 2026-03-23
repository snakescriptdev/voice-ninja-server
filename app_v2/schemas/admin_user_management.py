from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class UserManagementStats(BaseModel):
    total_users: int
    plan_distribution: List[dict] # [{"plan_name": str, "count": int}]

class UserManagementListItem(BaseModel):
    user_id: int
    username: str
    email: str
    plan_name: Optional[str]
    plan_id: Optional[int]
    balance_coins: int
    no_of_agents: int
    no_of_phones: int
    last_active: Optional[str]
    is_suspended: bool
    api_calls_total: int
    api_calls_monthly: int
    api_calls_weekly: int
    no_of_voices: int

    class Config:
        from_attributes = True

class SuspendUserRequest(BaseModel):
    is_suspended:bool
    reason: Optional[str]

class AdjustUserCoinRequest(BaseModel):

    coins:int
    reason:str