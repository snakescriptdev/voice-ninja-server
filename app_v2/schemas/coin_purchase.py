from pydantic import BaseModel,Field
from typing import Optional, Dict, Any

class OrderCreateRequest(BaseModel):
    bundle_id: int

class OrderCreateResponse(BaseModel):
    order_id: str
    amount: float = Field(...,gt=0)
    currency: str
    key_id: str
    user_email: str
    user_phone: str
    bundle_name: str

class OrderVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    bundle_id: int
