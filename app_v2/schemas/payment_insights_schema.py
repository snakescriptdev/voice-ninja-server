from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
from app_v2.schemas.enum_types import PaymentStatusEnum, PaymentTypeEnum

class PaymentItemSchema(BaseModel):
    id: int
    user_id: int
    user_name: Optional[str] = None
    amount: float
    currency: str
    status: PaymentStatusEnum
    payment_type: PaymentTypeEnum
    created_at: datetime
    plan_name: Optional[str] = None

    model_config = {"from_attributes": True}

class DailyTrendItem(BaseModel):
    date: str  # YYYY-MM-DD
    revenue: float

class RevenueItem(BaseModel):
    name: str # Plan name or bundle name
    revenue: float

class PaymentInsightsResponse(BaseModel):
    total_revenue_all_time: float
    total_revenue_monthly: float
    total_revenue_monthly_change: float
    successful_payments_count_all_time: int
    successful_payments_count_monthly: int
    successful_payments_count_monthly_change: float
    failed_payments_count_all_time: int
    failed_payments_count_monthly: int
    failed_payments_count_monthly_change: float
    daily_revenue_trend: List[DailyTrendItem]
    revenue_by_plan: List[RevenueItem]
    revenue_by_coin_bundle: List[RevenueItem]
    recent_transactions: List[PaymentItemSchema]
    recent_failed_payments: List[PaymentItemSchema]
