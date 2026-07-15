from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app_v2.schemas.pagination import PaginatedResponse
class UserCostItem(BaseModel):
    user_id: int
    user_name: str
    email: str
    total_cost: float

    model_config = {"from_attributes": True}

class AdminConversationItem(BaseModel):
    id: int
    created_at: datetime
    user_id: int
    user_name: str
    user_email: str
    agent_name: Optional[str] = None
    channel: Optional[str] = None
    call_status: Optional[str] = None
    duration: Optional[int] = None
    # ElevenLabs conversation id, for linking to the EL history page.
    elevenlabs_conv_id: Optional[str] = None
    # Actual total ElevenLabs cost for the call (EL credits) and what we
    # actually deducted from the user (coins).
    elevenlabs_cost: float
    coins_deducted: int
    # Actual ElevenLabs breakdown, split from post-call metadata (EL credits).
    actual_conversation_credits: Optional[float] = None
    actual_llm_credits: Optional[float] = None
    actual_telephony_cost: float = 0.0
    # Our live estimates for the same call (₹).
    calculated_conversation_cost: Optional[float] = None
    calculated_llm_cost: Optional[float] = None
    calculated_telephony_cost: Optional[float] = 0.0
    # (charged_₹ − our_cost_₹) / our_cost_₹ × 100. Negative = loss.
    profit_percentage: Optional[float] = None

    model_config = {"from_attributes": True}
