from pydantic import BaseModel,Field
from datetime import datetime

class CoinUsageSettingsResponse(BaseModel):
    id: int
    phone_number_purchase_cost: int
    elevenlabs_multiplier: float
    static_conversation_cost: int
    updated_at: datetime

    class Config:
        from_attributes = True

class CoinUsageSettingsUpdate(BaseModel):
    phone_number_purchase_cost: int | None = None
    elevenlabs_multiplier: float | None =  Field(gt=0)
    static_conversation_cost: int | None = None
