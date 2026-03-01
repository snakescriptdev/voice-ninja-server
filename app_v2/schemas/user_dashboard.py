from pydantic import BaseModel
from typing import List, Optional, Any
from app_v2.schemas.enum_types import BillingPeriodEnum, CoinTransactionTypeEnum, PaymentStatusEnum
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
    coins_used: int

class ChannelDistribution(BaseModel):
    channel: str
    count: int
    percentage: float

class DailyTrendSeries(BaseModel):
    date: str
    value: float

class UserAnalyticsResponse(BaseModel):
    total_calls: int
    avg_call_duration: float
    coin_used_this_month: int
    active_leads_count: int
    hourly_distribution: List[HourlyDistribution]
    agent_analytics: List[AgentAnalytics]
    channel_distribution: List[ChannelDistribution]
    call_trends: List[DailyTrendSeries]
    coin_trends: List[DailyTrendSeries]

class UserSubscriptionResponse(BaseModel):
    plan_id: int
    plan_name: str
    coins_included: int
    price: int
    billing_period: BillingPeriodEnum
    current_period_end: datetime
    class Config:
        from_attributes = True

class UserCoinUsageResponse(BaseModel):
    available_coins: int
    this_month_usage: int

# New Schemas for User Dashboard Refinement

class CoinBucketItem(BaseModel):
    source: str
    amount: int
    expiry_date: Optional[datetime] = None
    expiry_label: str = "No Expiry"

class CoinBucketsResponse(BaseModel):
    buckets: List[CoinBucketItem]
    total_available: int

class UsageHistoryItem(BaseModel):
    date_time: datetime
    action: str
    agent_name: Optional[str] = None
    coins_used: int
    balance: int

class UsageHistoryResponse(BaseModel):
    history: List[UsageHistoryItem]

class BillingHistoryItem(BaseModel):
    date: datetime
    description: str
    amount: float
    currency: str
    status: PaymentStatusEnum
    # invoice skip as requested

class BillingHistoryResponse(BaseModel):
    history: List[BillingHistoryItem]