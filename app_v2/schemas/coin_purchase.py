from pydantic import BaseModel,Field
from typing import Optional, Dict, Any, Literal

# Absolute floor only. The real, admin-configurable minimum lives in
# CoinUsageSettingsModel.minimum_purchase_amount_inr and is enforced in the
# route handlers, so it stays authoritative even if set below this value.
MIN_PURCHASE_AMOUNT = 1.0

class CreditEstimateResponse(BaseModel):
    amount: float
    credits: int

class PurchaseConfigResponse(BaseModel):
    minimum_purchase_amount_inr: float
    credits_per_rupee: float


class CallConfigResponse(BaseModel):
    # Minimum coin balance required to start/sustain a call.
    minimum_call_balance: int
    minimum_credits_per_minute: int
    minimum_call_minutes: int


BannerType = Literal["low_credits", "critical_credits"]

class CreditBannerStatusResponse(BaseModel):
    available_coins: int
    # Balance at/under which a call cannot be started (the "critical" banner).
    minimum_call_balance: int
    # Balance at/under which we warn a call may end mid-way (the "low" banner) —
    # a multiple of minimum_call_balance, always >= it.
    low_credits_threshold: int
    show_low_credits_banner: bool
    show_critical_credits_banner: bool

class DismissCreditBannerRequest(BaseModel):
    banner: BannerType

class OrderCreateRequest(BaseModel):
    amount: float = Field(..., ge=MIN_PURCHASE_AMOUNT)

class OrderCreateResponse(BaseModel):
    order_id: str
    amount: float = Field(...,gt=0)
    currency: str
    key_id: str
    user_email: str
    user_phone: str
    credits: int

class OrderVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
