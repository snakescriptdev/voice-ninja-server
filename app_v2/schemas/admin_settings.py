from pydantic import BaseModel,Field
from datetime import datetime

class CoinUsageSettingsResponse(BaseModel):
    id: int
    # What ElevenLabs charges us
    elevenlabs_conversation_credits_per_minute: int
    usd_to_credits: float
    # What we charge our users
    markup_percentage: float
    minimum_credits_per_minute: int
    minimum_call_minutes: int
    first_call_max_duration_seconds: int
    knowledge_base_llm_cost_multiplier: float
    tool_llm_cost_multiplier: float
    credits_per_rupee: float
    minimum_purchase_amount_inr: float
    updated_at: datetime

    class Config:
        from_attributes = True

class CoinUsageSettingsUpdate(BaseModel):
    # What ElevenLabs charges us
    elevenlabs_conversation_credits_per_minute: int | None = Field(default=None, ge=0)
    usd_to_credits: float | None = Field(default=None, ge=0)
    # What we charge our users
    markup_percentage: float | None = Field(default=None, ge=0)
    minimum_credits_per_minute: int | None = Field(default=None, ge=0)
    minimum_call_minutes: int | None = Field(default=None, ge=0)
    # First-call safety cap & LLM cost multipliers
    first_call_max_duration_seconds: int | None = Field(default=None, ge=0)
    knowledge_base_llm_cost_multiplier: float | None = Field(default=None, gt=0)
    tool_llm_cost_multiplier: float | None = Field(default=None, gt=0)
    credits_per_rupee: float | None = Field(default=None, gt=0)
    minimum_purchase_amount_inr: float | None = Field(default=None, ge=0)
