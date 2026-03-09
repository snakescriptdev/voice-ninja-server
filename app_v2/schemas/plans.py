from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from app_v2.schemas.enum_types import BillingPeriodEnum, PlanIconEnum, PaymentProviderEnum
import re


# -------------------- Plan Feature --------------------

class PlanFeatureBase(BaseModel):
    feature_key: str = Field(..., min_length=1)
    limit: Optional[int] = None

    @field_validator("feature_key")
    @classmethod
    def normalize_feature_key(cls, v: str):
        if not v or not v.strip():
            raise ValueError("feature_key cannot be empty")

        # remove extra spaces and normalize
        v = v.strip().lower()
        v = re.sub(r"\s+", "_", v)   # replace multiple spaces with _
        
        return v

    @field_validator("feature_key")
    @classmethod
    def validate_feature_key(cls, v: str):
        if not v.strip():
            raise ValueError("feature_key cannot be empty")
        return v.strip()

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v):
        if v is not None and v < 0:
            raise ValueError("limit must be greater than or equal to 0")
        return v


class PlanFeatureCreate(PlanFeatureBase):
    pass


class PlanFeatureResponse(PlanFeatureBase):
    id: int
    plan_id: int

    class Config:
        from_attributes = True


# -------------------- Plan Base --------------------

class PlanBase(BaseModel):
    display_name: str = Field(..., min_length=1)

    price: float = Field(..., gt=0)
    currency: str = Field(default="INR", min_length=1)
    description: Optional[str] = None
    coins_included: int = Field(default=0, ge=0)
    carry_forward_coins: bool = False
    billing_period: BillingPeriodEnum
    icon: PlanIconEnum
    gradient_color: str = Field(..., min_length=1)
    mark_as_popular: bool = False
    is_active: bool = True

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str):
        if not v.strip():
            raise ValueError("display_name cannot be empty")
        return v.strip()


    @field_validator("gradient_color")
    @classmethod
    def validate_gradient_color(cls, v: str):
        if not v.strip():
            raise ValueError("gradient_color cannot be empty")
        return v.strip()


# -------------------- Create --------------------

class PlanCreate(PlanBase):
    features: List[PlanFeatureCreate] = Field(..., min_length=1)


# -------------------- Update --------------------

class PlanUpdate(BaseModel):
    display_name: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    description: Optional[str] = None
    coins_included: Optional[int] = Field(default=None, ge=0)
    carry_forward_coins: Optional[bool] = None
    billing_period: Optional[BillingPeriodEnum] = None
    icon: Optional[PlanIconEnum] = None
    gradient_color: Optional[str] = None
    mark_as_popular: Optional[bool] = None
    is_active: Optional[bool] = None
    features: Optional[List[PlanFeatureCreate]] = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v):
        if v is not None and not v.strip():
            raise ValueError("display_name cannot be empty")
        return v.strip() if v else v


    @field_validator("gradient_color")
    @classmethod
    def validate_gradient_color(cls, v):
        if v is not None and not v.strip():
            raise ValueError("gradient_color cannot be empty")
        return v.strip() if v else v


# -------------------- Provider Response --------------------

class PlanProviderResponse(BaseModel):
    provider: PaymentProviderEnum
    provider_plan_id: str
    provider_price_id: Optional[str] = None
    provider_metadata: Optional[dict] = None
    is_active: bool

    class Config:
        from_attributes = True


# -------------------- Plan Response --------------------

class PlanResponse(PlanBase):
    id: int
    created_at: datetime
    modified_at: datetime
    features: List[PlanFeatureResponse]

    class Config:
        from_attributes = True