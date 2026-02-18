"""
ElevenLabs Conversation Utilities

This module provides utilities for managing conversation records with the ElevenLabs Conversational AI API.
Handles fetching conversation lists, details, audio, and deletion.
"""

from typing import Optional, Dict, Any, List
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


class ElevenLabsConversation(BaseElevenLabs):
    """
    Utility class for ElevenLabs Conversational AI conversation management.
    """

    def get_conversations(self, agent_id: Optional[str] = None, **kwargs) -> ElevenLabsResponse:
        """
        List all conversations, optionally filtered by agent_id.
        
        Args:
            agent_id: Optional ElevenLabs agent ID to filter by.
            **kwargs: Additional query parameters (cursor, call_successful, etc.)
            
        Returns:
            ElevenLabsResponse with list of conversations.
        """
        logger.info(f"Fetching conversations. Filter: agent_id={agent_id}")
        params = kwargs.copy()
        if agent_id:
            params["agent_id"] = agent_id
            
        response = self._get("/convai/conversations", params=params)
        
        if response.status:
            logger.info("✅ Conversations fetched successfully")
        else:
            logger.error(f"Failed to fetch conversations: {response.error_message}")
            
        return response

    def get_conversation(self, conversation_id: str) -> ElevenLabsResponse:
        """
        Get details for a specific conversation.
        
        Args:
            conversation_id: ElevenLabs conversation ID.
            
        Returns:
            ElevenLabsResponse with conversation details.
        """
        logger.info(f"Fetching conversation details: {conversation_id}")
        response = self._get(f"/convai/conversations/{conversation_id}")
        
        if response.status:
            logger.info(f"✅ Conversation details fetched for {conversation_id}")
        else:
            logger.error(f"Failed to fetch conversation {conversation_id}: {response.error_message}")
            
        return response

    def delete_conversation(self, conversation_id: str) -> ElevenLabsResponse:
        """
        Delete a specific conversation.
        
        Args:
            conversation_id: ElevenLabs conversation ID.
            
        Returns:
            ElevenLabsResponse indicating success or failure.
        """
        logger.info(f"Deleting conversation: {conversation_id}")
        response = self._delete(f"/convai/conversations/{conversation_id}")
        
        if response.status:
            logger.info(f"✅ Conversation {conversation_id} deleted")
        else:
            logger.error(f"Failed to delete conversation {conversation_id}: {response.error_message}")
            
        return response

    def get_conversation_audio(self, conversation_id: str) -> ElevenLabsResponse:
        """
        Fetch the audio recording for a conversation.
        
        Args:
            conversation_id: ElevenLabs conversation ID.
            
        Returns:
            ElevenLabsResponse with audio data.
        """
        logger.info(f"Fetching audio for conversation: {conversation_id}")
        response = self._get(f"/convai/conversations/{conversation_id}/audio", raw=True)
        
        if response.status:
            logger.info(f"✅ Audio fetched for conversation {conversation_id}")
        else:
            logger.error(f"Failed to fetch audio for conversation {conversation_id}: {response.error_message}")
            
        return response
    
    def extract_conversation_metadata(self, conversation_id: str, max_retries: int = 5, delay_seconds: float = 3.0) -> Dict[str, Any]:
        """
        Fetch conversation details from ElevenLabs and extract metadata for database storage.
        Retries if data is incomplete (async assembly by ElevenLabs).

        Args:
            conversation_id: ElevenLabs conversation ID.
            max_retries: Number of times to retry if data is incomplete.
            delay_seconds: Seconds to wait between retries.

        Returns:
            Dictionary with extracted metadata:
            - agent_name: Name of the agent
            - duration: Call duration in seconds
            - call_successful: Whether the call was successful
            - transcript_summary: Summary of the conversation
            - transcript: Full transcript (list of messages)
            - message_count: Total number of messages in transcript
        """
        import time
        logger.info(f"Extracting metadata for conversation: {conversation_id}")

        for attempt in range(1, max_retries + 1):
            response = self.get_conversation(conversation_id)

            if not response.status or not response.data:
                logger.error(f"Failed to fetch conversation metadata: {response.error_message}")
                return {}

            conv_data = response.data

            # Check if required fields are present and not empty
            has_metadata = bool(conv_data.get("metadata"))
            has_analysis = bool(conv_data.get("analysis"))
            transcript_data = conv_data.get("transcript", [])
            has_transcript = isinstance(transcript_data, list) and len(transcript_data) > 0

            if has_metadata and has_analysis and has_transcript:
                try:
                    metadata = {
                        "agent_name": conv_data.get("agent_name"),
                        "duration": (conv_data.get("metadata") or {}).get("call_duration_secs"),
                        "call_successful": (conv_data.get("analysis") or {}).get("call_successful", True),
                        "transcript_summary": (conv_data.get("analysis") or {}).get("transcript_summary"),
                    }

                    transcript_list = []
                    for idx, msg in enumerate(transcript_data):
                        transcript_list.append(
                            {
                                "role": msg.get("role", "user"),  # 'user' or 'agent'
                                "message": msg.get("message", ""),
                                "tool_calls": msg.get("tool_calls"),
                                "tool_result": msg.get("tool_results"),
                                "rag_retrieval_info": msg.get("rag_retrieval_info")
                            }
                        )
                    metadata["transcript"] = transcript_list
                    metadata["message_count"] = len(transcript_list)

                    logger.info(f"✅ Extracted metadata for conversation {conversation_id}: "
                                f"duration={metadata.get('duration')}s, messages={metadata.get('message_count')}")
                    return metadata
                except Exception as e:
                    logger.error(f"Error extracting conversation metadata: {str(e)}")
                    return {}
            else:
                logger.warning(f"Conversation data incomplete on attempt {attempt}/{max_retries}. "
                               f"metadata: {has_metadata}, analysis: {has_analysis}, transcript: {has_transcript}. Retrying after {delay_seconds}s...")
                if attempt < max_retries:
                    time.sleep(delay_seconds)
                else:
                    logger.error(f"Max retries reached. Conversation data still incomplete for {conversation_id}.")
                    return {}
    
