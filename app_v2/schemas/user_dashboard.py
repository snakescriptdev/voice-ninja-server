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
    total_duration: int
    amount_used: float

class WebAgentSummaryRef(BaseModel):
    id: int
    public_id: str
    web_agent_name: str

class WidgetSummaryRef(BaseModel):
    id: int
    public_id: str
    widget_name: str

class AgentSummaryItem(BaseModel):
    agent_id: int
    agent_name: str
    elevenlabs_agent_id: Optional[str] = None
    web_agent_count: int
    widget_count: int
    web_agents: List[WebAgentSummaryRef] = []
    widgets: List[WidgetSummaryRef] = []
    total_conversations: int
    success_count: int
    failed_count: int
    # Raw credits — used by the admin-side agents-summary view.
    total_credits_used: int
    # INR-converted equivalent of total_credits_used — used by the user-side
    # analytics page, which shows amount only (no credit/coin terminology).
    total_amount_used: float
    kb_count: int = 0
    tool_count: int = 0
    kb_total_pages: Optional[int] = None

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
    amount_used_this_month: float
    amount_used_this_month_change: float
    active_leads_count: int
    active_leads_count_change: float
    hourly_distribution: List[HourlyDistribution]
    agent_analytics: List[AgentAnalytics]
    channel_distribution: List[ChannelDistribution]
    call_trends: List[DailyTrendSeries]
    amount_trends: List[DailyTrendSeries]

class UserCoinUsageResponse(BaseModel):
    available_amount: float
    this_month_usage_amount: float

# New Schemas for User Dashboard Refinement

class CoinBucketItem(BaseModel):
    source: str
    amount: float

class CoinBucketsResponse(BaseModel):
    buckets: List[CoinBucketItem]
    total_available: float

class UsageHistoryItem(BaseModel):
    date_time: datetime
    action: str
    transaction_type: str
    agent_name: Optional[str] = None
    # Signed INR amount: positive = added, negative = deducted.
    amount: float
    balance_before: float
    balance_after: float
    # Reason an admin gave for this adjustment; null for non-admin entries.
    reason: Optional[str] = None

class UsageHistoryResponse(BaseModel):
    total: int
    page: int
    size: int
    pages: int
    history: List[UsageHistoryItem]

class BillingHistoryItem(BaseModel):
    # Null for non-payment subscription lifecycle events (see below) — those
    # have no PaymentModel row and therefore no invoice to download.
    payment_id: Optional[int] = None
    date: datetime
    description: str
    amount: float
    currency: str
    # PaymentStatusEnum for actual payments (pending/success/failed/refunded),
    # plus plain strings for non-payment subscription lifecycle events
    # (paused/cancelled/cancellation_scheduled) — see SubscriptionBillingEventEnum.
    status: str
    invoice_url: Optional[str] = None
    provider_payment_id: Optional[str] = None
    provider_order_id: Optional[str] = None

class BillingHistoryResponse(BaseModel):
    history: List[BillingHistoryItem]

class UserAPICallLogItem(BaseModel):
    id: int
    api_route: str
    status_code: int
    response_time_ms: Optional[float]
    amount_used: float
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
    api_amount_used_this_month: float
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

# ---- Public API / Public Websocket Logs page ----

class PublicLogEndpointItem(BaseModel):
    channel: str
    route: str
    method: Optional[str] = None
    success_count: int
    failure_count: int
    total_count: int

class PublicLogEndpointListResponse(BaseModel):
    endpoints: List[PublicLogEndpointItem]

class DayOfMonthBucket(BaseModel):
    day: int
    success_count: int
    failure_count: int

class PublicLogGraphResponse(BaseModel):
    month: str
    buckets: List[DayOfMonthBucket]

class PublicLogItem(BaseModel):
    id: int
    channel: Optional[str] = None
    api_route: str
    method: Optional[str] = None
    status_code: int
    is_success: Optional[bool] = None
    request_params: Optional[Any] = None
    request_body: Optional[Any] = None
    response_body: Optional[Any] = None
    error_message: Optional[str] = None
    response_time_ms: Optional[int] = None
    created_at: datetime
    api_key_id: Optional[int] = None
    api_key_name: Optional[str] = None

    class Config:
        from_attributes = True

class PublicLogOverviewResponse(BaseModel):
    total_calls: int
    success_count: int
    failure_count: int

class PublicLogListResponse(BaseModel):
    total: int
    page: int
    size: int
    pages: int
    items: List[PublicLogItem]
