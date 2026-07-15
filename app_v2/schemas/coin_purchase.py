from pydantic import BaseModel,Field
from typing import Optional, Dict, Any

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
