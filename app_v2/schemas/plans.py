from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from app_v2.schemas.enum_types import BillingPeriodEnum, PlanIconEnum, PaymentProviderEnum

class PlanFeatureBase(BaseModel):
    feature_key: str
    limit: Optional[int] = None
    is_unlimited: bool = False

class PlanFeatureCreate(PlanFeatureBase):
    pass

class PlanFeatureResponse(PlanFeatureBase):
    id: int
    plan_id: int

    class Config:
        from_attributes = True

class PlanBase(BaseModel):
    display_name: str
    internal_name: str
    price: float
    currency: str = "INR"
    coins_included: int = 0
    billing_period: BillingPeriodEnum
    icon: PlanIconEnum
    gradient_color: str
    mark_as_popular: bool = False
    is_active: bool = True

class PlanCreate(PlanBase):
    features: List[PlanFeatureCreate]

class PlanUpdate(BaseModel):
    display_name: Optional[str] = None
    internal_name: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    coins_included: Optional[int] = None
    billing_period: Optional[BillingPeriodEnum] = None
    icon: Optional[PlanIconEnum] = None
    gradient_color: Optional[str] = None
    mark_as_popular: Optional[bool] = None
    is_active: Optional[bool] = None
    features: Optional[List[PlanFeatureCreate]] = None

class PlanProviderResponse(BaseModel):
    provider: PaymentProviderEnum
    provider_plan_id: str
    provider_price_id: Optional[str] = None
    provider_metadata: Optional[dict] = None
    is_active: bool

    class Config:
        from_attributes = True

class PlanResponse(PlanBase):
    id: int
    created_at: datetime
    modified_at: datetime
    features: List[PlanFeatureResponse]
    providers: List[PlanProviderResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True
