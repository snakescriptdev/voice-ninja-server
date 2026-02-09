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
