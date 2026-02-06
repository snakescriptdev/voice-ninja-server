import os
from fastapi import APIRouter, Depends, HTTPException, status, Form, Request
from fastapi.responses import Response
from fastapi_sqlalchemy import db
from typing import List, Optional
from app_v2.utils.twillio_phone_service import TwilioPhoneService
from app_v2.utils.elevenlabs import ElevenLabsPhoneConnection
from app_v2.schemas.phone_schema import (
    PhoneNumberSearchRequest, 
    PhoneNumberBuyRequest, 
    PhoneNumberResponse, 
    AvailableNumberResponse,
    PhoneNumberAssignRequest,
    PhoneNumberUpdateWebhookRequest,
    TwilioVoiceWebhookData,
    TwilioCallStatusData,
    ElevenLabsSignedURLRequest,
    ElevenLabsSignedURLResponse
)
from app_v2.databases.models import PhoneNumberService, AgentModel, UnifiedAuthModel
from app_v2.utils.jwt_utils import HTTPBearer, get_current_user
from app_v2.schemas.enum_types import PhoneNumberAssignStatus
from app_v2.core.logger import setup_logger
from app_v2.core.config import VoiceSettings

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/phone",
    tags=["Phone Numbers"],
    dependencies=[Depends(HTTPBearer())]
)


# Helper function to construct webhook URLs
def get_webhook_base_url():
    """Get the base URL for webhooks from environment"""
    ngrok_url = VoiceSettings.NGROK_BASE_URL
    # Remove wss:// or ws:// and convert to https://
    if ngrok_url.startswith("wss://"):
        return ngrok_url.replace("wss://", "https://")
    elif ngrok_url.startswith("ws://"):
        return ngrok_url.replace("ws://", "http://")
    return ngrok_url

@router.get("/available", response_model=List[AvailableNumberResponse], openapi_extra={"security": [{"BearerAuth": []}]})
async def get_available_numbers(
    country_code: str,
    area_code: Optional[str] = None,
    limit: int = 10,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Search for available phone numbers in Twilio"""
    try:
        service = TwilioPhoneService()
        numbers = service.get_available_phone_numbers(country_code, area_code, limit)
        return numbers
    except Exception as e:
        logger.error(f"Error searching available numbers: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch available numbers from Twilio")

@router.post("/buy", response_model=PhoneNumberResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def buy_number(
    request: PhoneNumberBuyRequest,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Purchase a phone number from Twilio and associate with current user"""
    try:
        service = TwilioPhoneService()
        
        # Construct webhook URLs using NGROK_BASE_URL
        base_url = get_webhook_base_url()
        voice_url = f"{base_url}/api/v2/twilio/voice"
        
        logger.info(f"Purchasing phone number {request.phone_number} with webhooks: voice={voice_url}")
        
        # 1. Purchase from Twilio
        twilio_data = service.buy_phone_number(
            phone_number=request.phone_number,
            voice_url=voice_url
        )
        
        # 2. Save to DB with user_id
        with db():
            new_phone = PhoneNumberService(
                phone_number=twilio_data["phone_number"],
                sid=twilio_data["sid"],
                type="local",  # Could be mobile/toll-free depending on twilio response
                user_id=current_user.id,
                assigned_to=None,  # No agent assignment during purchase
                status=PhoneNumberAssignStatus.unassigned,
                monthly_cost=1.0,  # Placeholder, should come from Twilio or config
            )
            db.session.add(new_phone)
            db.session.commit()
            db.session.refresh(new_phone)
            logger.info(f"Phone number {new_phone.phone_number} purchased and saved for user {current_user.id}")
            return new_phone
            
    except Exception as e:
        logger.error(f"Error purchasing phone number: {str(e)}")
        if "HTTP" in str(getattr(e, "detail", "")):
             raise e
        raise HTTPException(status_code=500, detail=f"Failed to purchase phone number: {str(e)}")

@router.get("/list", response_model=List[PhoneNumberResponse], openapi_extra={"security": [{"BearerAuth": []}]})
async def list_phone_numbers(
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """List all phone numbers owned by the current user"""
    with db():
        numbers = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.user_id == current_user.id
        ).all()
        logger.info(f"Retrieved {len(numbers)} phone numbers for user {current_user.id}")
        return numbers



@router.patch("/{phone_id}/webhook", response_model=PhoneNumberResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def update_phone_webhook(
    phone_id: int,
    request: PhoneNumberUpdateWebhookRequest,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Update webhook URLs for an existing phone number"""
    with db():
        # Verify phone number exists and belongs to user
        phone = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.id == phone_id,
            PhoneNumberService.user_id == current_user.id
        ).first()
        if not phone:
            raise HTTPException(status_code=404, detail="Phone number not found or unauthorized")
        
        try:
            service = TwilioPhoneService()
            service.update_phone_number_webhook(
                sid=phone.sid,
                voice_url=request.voice_url
            )
            logger.info(f"Updated webhooks for phone {phone.phone_number}")
            return phone
        except Exception as e:
            logger.error(f"Failed to update webhooks: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to update webhooks: {str(e)}")

@router.delete("/{phone_id}", status_code=204, openapi_extra={"security": [{"BearerAuth": []}]})
async def delete_phone_number(
    phone_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Release a phone number from Twilio and delete from database"""
    with db():
        # Verify phone number exists and belongs to user
        phone = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.id == phone_id,
            PhoneNumberService.user_id == current_user.id
        ).first()
        if not phone:
            raise HTTPException(status_code=404, detail="Phone number not found or unauthorized")
        
        try:
            # Release from Twilio
            service = TwilioPhoneService()
            service.release_phone_number(phone.sid)
            
            # Delete from database
            db.session.delete(phone)
            db.session.commit()
            logger.info(f"Phone number {phone.phone_number} released and deleted for user {current_user.id}")
            return Response(status_code=204)
        except Exception as e:
            logger.error(f"Failed to release phone number: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to release phone number: {str(e)}")

@router.post("/elevenlabs/signed-url", response_model=ElevenLabsSignedURLResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def get_elevenlabs_signed_url(
    request: ElevenLabsSignedURLRequest,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Get a signed URL for connecting to an ElevenLabs agent via WebSocket"""
    with db():
        # Verify the agent exists and belongs to the user
        agent = db.session.query(AgentModel).filter(
            AgentModel.elevenlabs_agent_id == request.agent_id,
            AgentModel.user_id == current_user.id
        ).first()
        
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found or unauthorized")
        
        try:
            el_service = ElevenLabsPhoneConnection()
            response = el_service.get_signed_url(request.agent_id)
            
            if not response.status:
                raise HTTPException(status_code=500, detail=f"Failed to get signed URL: {response.error_message}")
            
            return ElevenLabsSignedURLResponse(
                signed_url=response.data.get("signed_url"),
                agent_id=request.agent_id
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting signed URL: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to get signed URL: {str(e)}")


# Twilio Webhook Endpoints (No authentication required - Twilio uses request validation)
twilio_router = APIRouter(
    prefix="/api/v2/twilio",
    tags=["Twilio Webhooks"]
)

@twilio_router.post("/voice")
async def handle_voice_webhook(request: Request):
    """Handle incoming Twilio voice calls"""
    try:
        # Parse form data from Twilio
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        logger.info(f"Incoming call: CallSid={call_sid}, From={from_number}, To={to_number}")
        
        # Look up phone number in database
        with db():
            phone = db.session.query(PhoneNumberService).filter(
                PhoneNumberService.phone_number == to_number
            ).first()
            
            if not phone:
                # Phone number not found, return default message
                logger.warning(f"Phone number {to_number} not found in database")
                twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, this number is not configured.</Say>
</Response>"""
                return Response(content=twiml, media_type="application/xml")
            
            # Check if phone is assigned to an agent
            if not phone.assigned_to:
                logger.info(f"Phone number {to_number} not assigned to any agent")
                twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Hello, this agent is not yet configured. Please try again later.</Say>
</Response>"""
                return Response(content=twiml, media_type="application/xml")
            
            # Get agent details
            agent = db.session.query(AgentModel).filter(
                AgentModel.id == phone.assigned_to
            ).first()
            
            if not agent or not agent.elevenlabs_agent_id:
                logger.error(f"Agent not found or missing ElevenLabs agent ID for phone {to_number}")
                twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, this service is temporarily unavailable.</Say>
</Response>"""
                return Response(content=twiml, media_type="application/xml")
            
            # Get ElevenLabs signed URL
            try:
                el_service = ElevenLabsPhoneConnection()
                el_response = el_service.get_signed_url(agent.elevenlabs_agent_id)
                
                if not el_response.status:
                    raise Exception(f"Failed to get signed URL: {el_response.error_message}")
                
                signed_url = el_response.data.get("signed_url")
                logger.info(f"Connecting call {call_sid} to ElevenLabs agent {agent.agent_name}")
                
                # Return TwiML to connect to ElevenLabs WebSocket
                twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{signed_url}" />
    </Connect>
</Response>"""
                return Response(content=twiml, media_type="application/xml")
                
            except Exception as e:
                logger.error(f"Error connecting to ElevenLabs: {str(e)}")
                twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, we encountered an error connecting to the agent. Please try again later.</Say>
</Response>"""
                return Response(content=twiml, media_type="application/xml")
    
    except Exception as e:
        logger.error(f"Error handling voice webhook: {str(e)}")
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, an error occurred. Please try again later.</Say>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

@twilio_router.post("/status")
async def handle_status_callback(request: Request):
    """Handle Twilio call status callbacks"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        call_duration = form_data.get("CallDuration")
        
        logger.info(f"Call status update: CallSid={call_sid}, Status={call_status}, Duration={call_duration}")
        
        # TODO: Store call logs in database for analytics
        
        return {"status": "received"}
    except Exception as e:
        logger.error(f"Error handling status callback: {str(e)}")
        return {"status": "error", "message": str(e)}
