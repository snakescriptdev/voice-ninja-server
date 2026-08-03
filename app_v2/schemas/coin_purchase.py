from pydantic import BaseModel,Field
from typing import Optional, Dict, Any, Literal

# Absolute floor only. The real minimum is admin-set — see
# CoinUsageSettingsModel.minimum_purchase_amount_inr — and is enforced in the
# route handlers, so it stays authoritative even if set below this value.
MIN_PURCHASE_AMOUNT = 1.0

# Hard ceiling on a single add-credits purchase, enforced regardless of what
# the admin-configured minimum is set to.
MAX_PURCHASE_AMOUNT = 100000.0

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
    # Max duration (seconds) allowed for an agent's very first call; 0 = no cap.
    first_call_max_duration_seconds: int


BannerType = Literal["low_credits", "critical_credits"]

class CreditBannerStatusResponse(BaseModel):
    # INR equivalent of the user's current balance.
    available_amount: float
    # INR equivalent of the balance at/under which a call cannot be started
    # (the "critical" banner).
    minimum_call_balance_amount: float
    show_low_credits_banner: bool
    show_critical_credits_banner: bool

class DismissCreditBannerRequest(BaseModel):
    banner: BannerType

class OrderCreateRequest(BaseModel):
    amount: float = Field(
        ...,
        gt=0,
        le=MAX_PURCHASE_AMOUNT,
        description=f"Amount in INR. Must be greater than 0 and at most {MAX_PURCHASE_AMOUNT:.0f}.",
    )

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
