import os
from twilio.rest import Client
from app_v2.core.logger import setup_logger
from app_v2.core.config import VoiceSettings

logger = setup_logger(__name__)

class TwilioPhoneService:
    def __init__(self):
        self.client = Client(VoiceSettings.TWILIO_ACCOUNT_SID,
        VoiceSettings.TWILIO_AUTH_TOKEN)
        logger.info(f"Twilio client initialized with account SID: {VoiceSettings.TWILIO_ACCOUNT_SID}")
        logger.info(f"Twilio client initialized with auth token: {VoiceSettings.TWILIO_AUTH_TOKEN}")
    
    def get_available_phone_numbers(
        self,
        country_code:str,
        area_code:str|None =None,
        limit:int =10
    ):

        kwargs = {
            "voice_enabled": True,
            "limit": limit
                }
        if area_code:
            kwargs["area_code"] = area_code
        
        
        
        numbers = self.client.available_phone_numbers(country_code).local.list(**kwargs)
        logger.info(f"Found {len(numbers)} available phone numbers in {country_code} with area code {area_code}")
        return [
                {
                    "phone_number": n.phone_number,
                    "friendly_name": n.friendly_name,
                    "capabilities": n.capabilities,
                }
                for n in numbers
        ]

    
    def buy_phone_number(
        self,
        phone_number: str,
        voice_url: str,

    ):
        try:
            number = self.client.incoming_phone_numbers.create(
                phone_number= phone_number,
                voice_url=voice_url,
                voice_method="POST"
            )
            logger.info(f"Successfully bought phone number: {number.phone_number}")
            return {
                "sid": number.sid,
                "phone_number": number.phone_number,
                "friendly_name": number.friendly_name,
                "capabilities": number.capabilities,
            }
        except Exception as e:
            logger.error(f"Failed to buy phone number: {str(e)}")
            raise 

    def release_phone_number(self, sid: str):
        """Release a phone number from the Twilio account."""
        try:
            self.client.incoming_phone_numbers(sid).delete()
            logger.info(f"Successfully released phone number SID: {sid}")
            return True
        except Exception as e:
            logger.error(f"Failed to release phone number {sid}: {str(e)}")
            raise

    def update_phone_number_webhook(self, sid: str, voice_url: str = None):
        """Update the webhook URLs for an existing phone number."""
        try:
            update_kwargs = {}
            if voice_url:
                update_kwargs["voice_url"] = voice_url
                update_kwargs["voice_method"] = "POST"
            
            if not update_kwargs:
                return False

            number = self.client.incoming_phone_numbers(sid).update(**update_kwargs)
            logger.info(f"Successfully updated webhooks for phone number SID: {sid}")
            return True
        except Exception as e:
            logger.error(f"Failed to update webhooks for phone number {sid}: {str(e)}")
            raise

