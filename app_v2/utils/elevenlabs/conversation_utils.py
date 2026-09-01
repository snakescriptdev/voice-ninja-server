"""
ElevenLabs Conversation Utilities

This module provides utilities for managing conversation records with the ElevenLabs Conversational AI API.
Handles fetching conversation lists, details, audio, and deletion.
"""

from typing import Optional, Dict, Any, List
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger
from app_v2.utils.log_sanitizer import redact

logger = setup_logger(__name__)


def build_metadata_from_conv_data(conv_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Shared field-path mapping from an ElevenLabs conversation object to our
    internal metadata dict — used both by extract_conversation_metadata()
    (polling GET /convai/conversations/{id}) and by the post-call webhook
    handler (app_v2/routers/elevenlabs_webhook.py), since ElevenLabs' webhook
    payload's `data` field is the same conversation object shape as the GET
    response body (confirmed against a real captured webhook delivery).

    Returns {} if metadata/analysis/transcript are missing or incomplete —
    callers decide what to do next (retry, or treat as unrecoverable).
    """
    has_metadata = bool(conv_data.get("metadata"))
    has_analysis = bool(conv_data.get("analysis"))
    transcript_data = conv_data.get("transcript", [])
    has_transcript = isinstance(transcript_data, list) and len(transcript_data) > 0

    if not (has_metadata and has_analysis and has_transcript):
        return {}

    try:
        el_metadata = conv_data.get("metadata") or {}
        charging = el_metadata.get("charging") or {}
        metadata = {
            "agent_name": conv_data.get("agent_name"),
            "duration": el_metadata.get("call_duration_secs"),
            # ElevenLabs reports this as the string "success"/"failure"/
            # "unknown", not a boolean — an earlier truthiness check here
            # would have treated "failure" as truthy too.
            "call_successful": (conv_data.get("analysis") or {}).get("call_successful") == "success",
            "transcript_summary": (conv_data.get("analysis") or {}).get("transcript_summary"),
            # Total ElevenLabs cost for the call, in EL credits.
            "cost": el_metadata.get("cost"),
            # LLM portion of that cost (EL credits), 0 if unavailable.
            "llm_credits": _extract_llm_credits(charging),
            "total_llm_usd_price": charging.get("llm_price"),
        }

        transcript_list = []
        for msg in transcript_data:
            transcript_list.append(
                {
                    "role": msg.get("role", "user"),  # 'user' or 'agent'
                    "message": msg.get("message", ""),
                    # tool_calls carries the raw outbound webhook request
                    # ElevenLabs made for each tool (URL, headers, body) -
                    # for our own system tools (e.g. the personal-KB
                    # search webhook) that includes the internal
                    # Authorization bearer secret we configured on the
                    # tool. redact() strips that before this ever leaves
                    # the server, since it's a static, account-wide
                    # secret that also guards other internal endpoints.
                    "tool_calls": redact(msg.get("tool_calls")),
                    "tool_result": msg.get("tool_results"),
                    "rag_retrieval_info": msg.get("rag_retrieval_info"),
                }
            )
        metadata["transcript"] = transcript_list
        metadata["message_count"] = len(transcript_list)
        # Split by role for LLM-cost calibration: cost tracks turn
        # count (every turn re-sends the whole history), and user
        # vs. agent turns can differ (e.g. multi-part replies).
        metadata["user_message_count"] = sum(1 for t in transcript_list if t["role"] == "user")
        metadata["agent_message_count"] = sum(1 for t in transcript_list if t["role"] == "agent")
        return metadata
    except Exception as e:
        logger.error(f"Error extracting conversation metadata: {str(e)}")
        return {}


def _extract_llm_credits(charging: Dict[str, Any]) -> float:
    """
    Best-effort pull of the LLM portion (in EL credits) out of the conversation
    metadata's `charging` block. ElevenLabs reports the "Credits (LLM)" figure
    here; the exact key has varied, so we probe the known candidates and fall
    back to 0 (whole cost then attributed to conversation). The raw charging
    block is logged so the precise field can be confirmed against a live call.
    """
    if not isinstance(charging, dict):
        return 0.0
    for key in ("llm_charge", "llm_credits", "llm_cost", "llm_price_credits"):
        value = charging.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


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
    
    def extract_conversation_metadata(self, conversation_id: str, max_retries: int = 10, delay_seconds: float = 4.0) -> Dict[str, Any]:
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

            has_metadata = bool(conv_data.get("metadata"))
            has_analysis = bool(conv_data.get("analysis"))
            transcript_data = conv_data.get("transcript", [])
            has_transcript = isinstance(transcript_data, list) and len(transcript_data) > 0

            if has_metadata and has_analysis and has_transcript:
                # A genuine mapping failure here (not "data incomplete yet")
                # means the shape is wrong / a bug — return immediately
                # rather than burning through retries that would all fail
                # identically.
                metadata = build_metadata_from_conv_data(conv_data)
                if metadata:
                    logger.info(f"✅ Extracted metadata for conversation {conversation_id}: "
                                f"duration={metadata.get('duration')}s, messages={metadata.get('message_count')}")
                return metadata
            else:
                logger.warning(f"Conversation data incomplete on attempt {attempt}/{max_retries}. "
                               f"metadata: {has_metadata}, analysis: {has_analysis}, transcript: {has_transcript}. Retrying after {delay_seconds}s...")
                if attempt < max_retries:
                    time.sleep(delay_seconds)
                else:
                    logger.error(f"Max retries reached. Conversation data still incomplete for {conversation_id}.")
                    return {}
    
