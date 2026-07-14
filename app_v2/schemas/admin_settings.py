from pydantic import BaseModel,Field
from datetime import datetime

class CoinUsageSettingsResponse(BaseModel):
    id: int
    markup_percentage: float
    estimated_coins_per_minute: int
    minimum_call_minutes: int
    credits_per_rupee: float
    updated_at: datetime

    class Config:
        from_attributes = True

class CoinUsageSettingsUpdate(BaseModel):
    markup_percentage: float | None = Field(default=None, ge=0)
    estimated_coins_per_minute: int | None = Field(default=None, ge=0)
    minimum_call_minutes: int | None = Field(default=None, ge=0)
    credits_per_rupee: float | None = Field(default=None, gt=0)
