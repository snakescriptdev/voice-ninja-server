from pydantic import BaseModel
from typing import Optional
from app_v2.schemas.enum_types import PaymentProviderEnum

class SubscriptionCreate(BaseModel):
    plan_id: int

class SubscriptionResponse(BaseModel):
    subscription_id: str
    amount: float
    currency: str
    plan_id: int
    plan_name: str
    user_email: str
    user_phone: Optional[str] = None
    key_id: str

class SubscriptionVerifyRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str
    plan_id: int

class SubscriptionCancelRequest(BaseModel):
    cancel_at_cycle_end: bool = True

class SubscriptionUpdateRequest(BaseModel):
    plan_id: int

class SubscriptionPauseRequest(BaseModel):
    pause_at: str = "now"

class InvoiceItemResponse(BaseModel):
    id: str
    amount: float
    status: str
    date: Optional[int] = None
    invoice_url: Optional[str] = None
    description: Optional[str] = None

class InvoiceListResponse(BaseModel):
    invoices: list[InvoiceItemResponse]
