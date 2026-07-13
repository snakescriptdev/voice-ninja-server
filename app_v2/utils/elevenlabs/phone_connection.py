"""
ElevenLabs Phone Connection Utilities

This module provides utilities for connecting phone calls to ElevenLabs conversational agents.
Handles signed URL generation for WebSocket connections.
"""

from typing import Optional
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


class ElevenLabsPhoneConnection(BaseElevenLabs):
    """
    Phone connection utility class for ElevenLabs API operations.
    Handles phone call connections to conversational agents.
    """
    
    def get_signed_url(self, agent_id: str) -> ElevenLabsResponse:
        """
        Get a signed URL for connecting to an ElevenLabs conversational agent via WebSocket.
        
        This signed URL is used by Twilio to establish a bidirectional audio connection
        between the phone call and the ElevenLabs agent.
        
        Args:
            agent_id: ElevenLabs agent ID to connect to
            
        Returns:
            ElevenLabsResponse with signed_url on success
            
        Example response:
            {
                "signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?agent_id=xxx&signature=yyy"
            }
        """
        logger.info(f"Getting signed URL for agent: {agent_id}")
        
        response = self._get(f"/convai/conversation/get_signed_url?agent_id={agent_id}")
        
        if response.status:
            signed_url = response.data.get("signed_url")
            logger.info(f"✅ Signed URL obtained for agent: {agent_id}")
            logger.debug(f"Signed URL: {signed_url[:50]}...")  # Log partial URL for security
        else:
            logger.error(f"Failed to get signed URL for agent {agent_id}: {response.error_message}")

        return response

    def import_twilio_number(
        self,
        phone_number: str,
        label: str,
        account_sid: str,
        auth_token: str,
        agent_id: Optional[str] = None,
    ) -> ElevenLabsResponse:
        """
        Import/register a Twilio phone number with ElevenLabs Conversational AI.

        Args:
            phone_number: E.164 phone number
            label: Human-readable label for the number in ElevenLabs
            account_sid: Twilio Account SID that owns the number
            auth_token: Twilio Auth Token for that account
            agent_id: Optional ElevenLabs agent ID to assign the number to immediately

        Returns:
            ElevenLabsResponse with data={"phone_number_id": "..."} on success
        """
        logger.info(f"Importing Twilio number {phone_number} into ElevenLabs")

        payload = {
            "phone_number": phone_number,
            "label": label,
            "sid": account_sid,
            "token": auth_token,
            "provider": "twilio",
        }
        if agent_id:
            payload["agent_id"] = agent_id

        response = self._post("/convai/phone-numbers", data=payload)

        if response.status:
            logger.info(f"✅ Twilio number {phone_number} imported into ElevenLabs")
        else:
            logger.error(f"Failed to import Twilio number {phone_number} into ElevenLabs: {response.error_message}")

        return response

    def update_phone_number_agent(self, phone_number_id: str, agent_id: Optional[str]) -> ElevenLabsResponse:
        """
        Assign or unassign the agent linked to an already-imported ElevenLabs phone number.

        Args:
            phone_number_id: ElevenLabs phone_number_id returned by import_twilio_number
            agent_id: ElevenLabs agent ID to assign, or None to unassign

        Returns:
            ElevenLabsResponse with the updated phone number details on success
        """
        logger.info(f"Updating ElevenLabs phone number {phone_number_id} -> agent_id={agent_id}")

        response = self._patch(f"/convai/phone-numbers/{phone_number_id}", data={"agent_id": agent_id})

        if not response.status:
            logger.error(f"Failed to update ElevenLabs phone number {phone_number_id}: {response.error_message}")

        return response

    def delete_phone_number(self, phone_number_id: str) -> ElevenLabsResponse:
        """
        Delete an imported phone number from ElevenLabs Conversational AI.

        Args:
            phone_number_id: ElevenLabs phone_number_id returned by import_twilio_number

        Returns:
            ElevenLabsResponse indicating success/failure
        """
        logger.info(f"Deleting ElevenLabs phone number {phone_number_id}")

        response = self._delete(f"/convai/phone-numbers/{phone_number_id}")

        if response.status:
            logger.info(f"✅ ElevenLabs phone number {phone_number_id} deleted")
        else:
            logger.error(f"Failed to delete ElevenLabs phone number {phone_number_id}: {response.error_message}")

        return response
