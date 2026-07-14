from pydantic import BaseModel,Field
from typing import Optional, Dict, Any

MIN_PURCHASE_AMOUNT = 50.0

class CreditEstimateResponse(BaseModel):
    amount: float
    credits: int

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
