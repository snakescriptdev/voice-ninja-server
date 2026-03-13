from pydantic import BaseModel
from typing import List, Optional, Any
from app_v2.schemas.enum_types import BillingPeriodEnum, CoinTransactionTypeEnum, PaymentStatusEnum
from datetime import datetime
from app_v2.schemas.plans import PlanFeatureResponse
from app_v2.schemas.enum_types import SubscriptionStatusEnum,BillingPeriodEnum,PlanIconEnum

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
    total_calls_change: float
    avg_call_duration: float
    avg_call_duration_change: float
    coin_used_this_month: int
    coin_used_this_month_change: float
    active_leads_count: int
    active_leads_count_change: float
    hourly_distribution: List[HourlyDistribution]
    agent_analytics: List[AgentAnalytics]
    channel_distribution: List[ChannelDistribution]
    call_trends: List[DailyTrendSeries]
    coin_trends: List[DailyTrendSeries]

class UserSubscriptionResponse(BaseModel):
    # ---- Subscription fields ----
    subscription_id: int
    status: SubscriptionStatusEnum
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    provider: str
    provider_subscription_id: Optional[str]
    marked_for_update: bool = False
    next_plan_id: Optional[int] = None

    # ---- Plan fields ----
    plan_id: int
    plan_name: str
    description: Optional[str]
    price: float
    currency: str
    coins_included: int
    carry_forward_coins: bool
    billing_period: BillingPeriodEnum
    icon: PlanIconEnum
    gradient_color: str
    mark_as_popular: bool
    is_active: bool

    # ---- Features ----
    features: List[PlanFeatureResponse]

class UserCoinUsageResponse(BaseModel):
    available_coins: int
    this_month_usage: int

# New Schemas for User Dashboard Refinement

class CoinBucketItem(BaseModel):
    source: str
    amount: int
    expiry_date: Optional[datetime] = None
    status: Optional[str] = None

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
    invoice_url: Optional[str] = None

class BillingHistoryResponse(BaseModel):
    history: List[BillingHistoryItem]

class UserAPICallLogItem(BaseModel):
    id: int
    api_route: str
    status_code: int
    response_time_ms: Optional[float]
    coins_used: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class UserAPICallLogResponse(BaseModel):
    total: int
    page: int
    size: int
    pages: int
    logs: List[UserAPICallLogItem]

class APIUsageDailyItem(BaseModel):
    date: str
    count: int

class APIListItem(BaseModel):
    path: str
    method: str
    description: str
    swagger_link: str

class PublicAPIUsageResponse(BaseModel):
    total_api_calls_this_month: int
    total_api_calls_this_month_change: float
    api_coins_used_this_month: int
    avg_api_response_time_24h: float
    daily_usage: List[APIUsageDailyItem]
    api_list: List[APIListItem]
