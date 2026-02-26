from pydantic import BaseModel
from typing import List, Optional
from app_v2.schemas.enum_types import BillingPeriodEnum
from datetime import datetime

class UserDashboardAgentResponse(BaseModel):
    id: int
    agent_name: str
    is_enabled: bool


class UserDashboardPhoneNumberResponse(BaseModel):
    id: int
    phone_number: str

class HourlyDistribution(BaseModel):
    hour: int
    time_label: str
    count: int

class AgentAnalytics(BaseModel):
    agent_id: int
    agent_name: str
    call_count: int
    avg_duration: float

class ChannelDistribution(BaseModel):
    channel: str
    count: int
    percentage: float

class UserAnalyticsResponse(BaseModel):
    total_calls: int
    avg_call_duration: float
    hourly_distribution: List[HourlyDistribution]
    agent_analytics: List[AgentAnalytics]
    channel_distribution: List[ChannelDistribution]

class UserSubscriptionResponse(BaseModel):
    plan_id: int
    plan_name: str
    coins_included: int
    price: int
    billing_period: BillingPeriodEnum
    current_period_end: datetime
    class Config:
        from_attributes = True