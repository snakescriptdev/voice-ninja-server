from pydantic import BaseModel, Field,field_validator
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
    reason: Optional[str] = Field(max_length=1000,min_length=3,default=None)

class AdjustUserCoinRequest(BaseModel):

    coins:int
    reason:str = Field(...,max_length=1000,min_length=3)
    validity: Optional[int] = Field(gt=0,default=None)

    @field_validator("coins")
    @classmethod
    def validate_max_coins_to_add(cls,v:int):
        if v> 100000:
            raise ValueError("Coins to add cannot be more than 100000")
        return v