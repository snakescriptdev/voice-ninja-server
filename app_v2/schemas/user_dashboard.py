from pydantic import BaseModel, field_serializer
from typing import List, Optional, Any
from app_v2.schemas.enum_types import CoinTransactionTypeEnum
from datetime import datetime, date

class UserDashboardAgentResponse(BaseModel):
    id: int
    agent_name: str
    is_enabled: bool
    calls: int


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

class AgentSummaryItem(BaseModel):
    agent_id: int
    agent_name: str
    elevenlabs_agent_id: Optional[str] = None
    web_agent_count: int
    widget_count: int
    total_conversations: int
    success_count: int
    failed_count: int
    total_credits_used: int

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

class UserCoinUsageResponse(BaseModel):
    available_coins: int
    this_month_usage: int

# New Schemas for User Dashboard Refinement

class CoinBucketItem(BaseModel):
    source: str
    amount: int
    expiry_date: Optional[datetime] = None
    status: Optional[str] = None

    @field_serializer("expiry_date")
    def serialize_datetime(self,dt:datetime):
        if dt is not None:
            return dt.date()
        return None

class CoinBucketsResponse(BaseModel):
    buckets: List[CoinBucketItem]
    total_available: int

class UsageHistoryItem(BaseModel):
    date_time: datetime
    action: str
    transaction_type: str
    agent_name: Optional[str] = None
    # Signed: positive = credits added, negative = credits deducted.
    coins: int
    balance_before: int
    balance_after: int

class UsageHistoryResponse(BaseModel):
    history: List[UsageHistoryItem]

class BillingHistoryItem(BaseModel):
    date: datetime
    description: str
    amount: float
    currency: str
    # PaymentStatusEnum for actual payments (pending/success/failed/refunded),
    # plus plain strings for non-payment subscription lifecycle events
    # (paused/cancelled/cancellation_scheduled) — see SubscriptionBillingEventEnum.
    status: str
    invoice_url: Optional[str] = None

    @field_serializer("date")
    def serialize_datetime(self,dt:datetime):
        return dt.date()

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

class DashboardLeadItem(BaseModel):
    id: int
    widget_id: int
    widget_name: str
    widget_public_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    custom_data: Optional[Any] = None
    created_at: datetime
    duration: int = 0

    @field_serializer("created_at")
    def serialize_datetime(self,dt:datetime):
        return dt.date()

class DashboardLeadListResponse(BaseModel):
    total: int
    page: int
    size: int
    pages: int
    leads: List[DashboardLeadItem]
