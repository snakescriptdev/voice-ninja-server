from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict
from datetime import datetime
from app_v2.schemas.enum_types import PhoneNumberAssignStatus

class PhoneNumberSearchRequest(BaseModel):
    country_code: str = Field(..., description="ISO country code (e.g., US, GB)")
    area_code: Optional[str] = Field(None, description="Area code to search in")
    limit: Optional[int] = Field(10, ge=1, le=50)

class PhoneNumberBuyRequest(BaseModel):
    phone_number: str = Field(..., description="The phone number to purchase in E.164 format")

class PhoneNumberAssignRequest(BaseModel):
    phone_number_id: int
    agent_id: int

class PhoneNumberUpdateWebhookRequest(BaseModel):
    voice_url: Optional[str] = Field(None, description="URL for voice webhook")

class PhoneNumberResponse(BaseModel):
    id: int
    phone_number: str
    type: str
    status: PhoneNumberAssignStatus
    user_id: int
    assigned_to: Optional[int] = None
    monthly_cost: float
    created_at: datetime
    
    class Config:
        from_attributes = True

class AvailableNumberResponse(BaseModel):
    phone_number: str
    friendly_name: str
    capabilities: Dict[str, bool]

# Twilio Webhook Schemas
class TwilioVoiceWebhookData(BaseModel):
    """Schema for incoming Twilio voice webhook"""
    CallSid: str
    From: str
    To: str
    CallStatus: Optional[str] = None
    Direction: Optional[str] = None
    
class TwilioCallStatusData(BaseModel):
    """Schema for Twilio call status callbacks"""
    CallSid: str
    CallStatus: str
    CallDuration: Optional[str] = None
    From: Optional[str] = None
    To: Optional[str] = None

# ElevenLabs Connection Schemas
class ElevenLabsSignedURLRequest(BaseModel):
    """Request schema for getting ElevenLabs signed URL"""
    agent_id: str = Field(..., description="ElevenLabs agent ID")

class ElevenLabsSignedURLResponse(BaseModel):
    """Response schema with signed URL for WebSocket connection"""
    signed_url: str
    agent_id: str

class PhoneNumberImportRequest(BaseModel):
    """Request schema for importing an existing Twilio phone number"""
    phone_number: str = Field(..., description="The phone number in E.164 format")
    account_sid: str = Field(..., description="Twilio Account SID")
    auth_token: str = Field(..., description="Twilio Auth Token")

