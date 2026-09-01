from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime
from app_v2.schemas.pagination import PaginatedResponse
class UserCostItem(BaseModel):
    user_id: int
    user_name: str
    email: str
    total_cost: float

    model_config = {"from_attributes": True}

class ElevenLabsCreditBannerResponse(BaseModel):
    """Whether the low-ElevenLabs-credits header banner should be shown to
    admins right now — see admin_dashboard.get_elevenlabs_credit_banner."""
    credits_left: int
    show_banner: bool


class ConversationSettingsSnapshot(BaseModel):
    """
    The exact billing-relevant CoinUsageSettingsModel values in effect when a
    conversation was finalized/charged — see CoinUsageSettingsVersionModel.
    """
    version_number: int
    elevenlabs_conversation_credits_per_minute: int
    usd_to_credits: float
    markup_percentage: float
    minimum_credits_per_minute: int
    minimum_call_minutes: int
    first_call_max_duration_seconds: int
    knowledge_base_llm_cost_multiplier: float
    tool_llm_cost_multiplier: float
    credits_per_rupee: Optional[float] = None

    model_config = {"from_attributes": True}

class AdminConversationItem(BaseModel):
    id: int
    created_at: datetime
    user_id: int
    user_name: str
    user_email: str
    agent_name: Optional[str] = None
    # ElevenLabs agent id, for linking the agent name to its EL agent page.
    elevenlabs_agent_id: Optional[str] = None
    channel: Optional[str] = None
    call_status: Optional[str] = None
    duration: Optional[int] = None
    # ElevenLabs conversation id, for linking to the EL history page.
    elevenlabs_conv_id: Optional[str] = None
    # Actual total ElevenLabs cost for the call (EL credits) and what we
    # actually deducted from the user (coins).
    elevenlabs_cost: float
    coins_deducted: int
    # INR amount actually charged, computed at call-finalize time using the
    # rate frozen on that conversation's settings_version snapshot (see
    # coins_to_inr()/get_credits_per_rupee() in coin_utils.py) — NOT today's
    # live rate, which can differ if pricing changed since this call.
    cost_inr: Optional[float] = None
    # Actual ElevenLabs breakdown, split from post-call metadata (EL credits).
    actual_conversation_credits: Optional[float] = None
    actual_llm_credits: Optional[float] = None
    actual_telephony_cost: float = 0.0
    # Our live estimates for the same call (₹).
    calculated_conversation_cost: Optional[float] = None
    calculated_llm_cost: Optional[float] = None
    calculated_telephony_cost: Optional[float] = 0.0
    # (charged_₹ − our_cost_₹) / our_cost_₹ × 100. Negative = loss.
    profit_percentage: Optional[float] = None

    # LLM cost calibration snapshot, frozen at call time (see
    # ConversationsModel calibration columns / finalize_conversation()).
    user_message_count: Optional[int] = None
    agent_message_count: Optional[int] = None
    system_prompt_length: Optional[int] = None
    system_prompt_tokens: Optional[int] = None
    tool_count: Optional[int] = None
    kb_total_pages: Optional[int] = None
    rag_enabled: Optional[bool] = None

    # Which billing-settings version this call was charged under (see
    # coin_usage_settings_versions) — null for calls that predate this feature.
    settings_version: Optional[ConversationSettingsSnapshot] = None

    model_config = {"from_attributes": True}

class MonthlyProfitLossItem(BaseModel):
    """
    One month's profit/loss breakdown over conversations with a computed
    profit_percentage (calls with no cost data yet are excluded from both
    buckets and from total_calls).
    """
    month: str  # "YYYY-MM"
    total_calls: int
    profit_call_count: int
    loss_call_count: int
    # Share of total_calls that were profitable / a loss (sums to ~100%).
    profit_pct_share: float
    loss_pct_share: float
    # Mean profit_percentage among profit calls / loss calls this month (the
    # magnitude, not the share) — null if that month has no calls of that kind.
    avg_profit_percentage: Optional[float] = None
    avg_loss_percentage: Optional[float] = None

class OverallProfitLossSummary(BaseModel):
    """Same shape as MonthlyProfitLossItem but aggregated across every month, plus per-month averages."""
    total_calls: int
    profit_call_count: int
    loss_call_count: int
    profit_pct_share: float
    loss_pct_share: float
    avg_profit_percentage: Optional[float] = None
    avg_loss_percentage: Optional[float] = None
    months_count: int
    avg_profit_call_count_per_month: float
    avg_loss_call_count_per_month: float

class ProfitLossAnalyticsResponse(BaseModel):
    # Most recent month first.
    months: List[MonthlyProfitLossItem]
    overall: OverallProfitLossSummary

# ---- Admin: Public API / Public Websocket Logs dashboard ----

class AdminPublicLogEndpointItem(BaseModel):
    channel: str
    route: str
    method: Optional[str] = None
    success_count: int
    failure_count: int
    total_count: int

class AdminPublicLogEndpointListResponse(BaseModel):
    endpoints: List[AdminPublicLogEndpointItem]

class AdminPublicLogItem(BaseModel):
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
    user_id: int
    user_name: Optional[str] = None
    user_email: Optional[str] = None

    model_config = {"from_attributes": True}

class AdminWebhookEventItem(BaseModel):
    id: int
    provider: str
    event_id: str
    event_type: str
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None
    # Extracted from the raw payload / resolved via the addon order it belongs to.
    payment_id: Optional[str] = None
    order_id: Optional[str] = None
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    payload: Optional[dict] = None

    model_config = {"from_attributes": True}

class AdminPaymentItem(BaseModel):
    # "payment" (a real PaymentModel transaction) or "admin_adjustment" (a
    # manual CoinsLedgerModel credit/debit an admin made, no money involved).
    entry_type: str
    payment_id: int
    user_id: int
    user_email: str
    date: datetime
    description: str
    # amount/currency populated for entry_type="payment" only.
    amount: Optional[float] = None
    currency: Optional[str] = None
    # coins populated for entry_type="admin_adjustment" only (signed).
    coins: Optional[int] = None
    status: Optional[str] = None
    provider: Optional[str] = None
    provider_payment_id: Optional[str] = None
    provider_order_id: Optional[str] = None
    # Admin-provided reason for entry_type="admin_adjustment" only.
    reason: Optional[str] = None

class AdminPublicLogUserItem(BaseModel):
    user_id: int
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    failure_count: int
    total_count: int

class AdminPublicLogUserListResponse(BaseModel):
    users: List[AdminPublicLogUserItem]
