"""
ElevenLabs Voice Utilities

This module provides utilities for voice-related operations with the ElevenLabs API.
Handles voice creation, retrieval, updating, and deletion.
"""

from typing import Optional, Dict, Any, List
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


class ElevenLabsVoice(BaseElevenLabs):
    """
    Voice utility class for ElevenLabs API operations.
    Handles all voice-related API calls including cloning, fetching, and managing voices.
    """
    
    def create_cloned_voice(self, file_path: str, name: str, description: str = "", 
                           labels: Optional[Dict[str, str]] = None) -> ElevenLabsResponse:
        """
        Clone a voice by uploading an audio file to ElevenLabs.
        
        Args:
            file_path: Path to the audio file for voice cloning
            name: Name for the cloned voice
            description: Optional description for the voice
            labels: Optional metadata labels for the voice
            
        Returns:
            ElevenLabsResponse with voice_id on success
        """
        try:
            logger.info(f"Cloning voice: {name} from file: {file_path}")
            
            with open(file_path, "rb") as audio_file:
                files = {"files": audio_file}
                data = {"name": name}
                
                if description:
                    data["description"] = description
                if labels:
                    import json
                    data["labels"] = json.dumps(labels)
                
                response = self._post("/voices/add", data=data, files=files)
                
                if response.status:
                    voice_id = response.data.get("voice_id")
                    logger.info(f"✅ Voice cloned successfully: {name} (ID: {voice_id})")
                    return ElevenLabsResponse(status=True, data={"voice_id": voice_id, "name": name})
                else:
                    logger.error(f"Failed to clone voice: {response.error_message}")
                    return response
                    
        except FileNotFoundError:
            error_msg = f"Audio file not found: {file_path}"
            logger.error(error_msg)
            return ElevenLabsResponse(status=False, error_message=error_msg)
        except Exception as e:
            error_msg = f"Error cloning voice: {str(e)}"
            logger.error(error_msg)
            return ElevenLabsResponse(status=False, error_message=error_msg)
    
    def get_voice(self, voice_id: str) -> ElevenLabsResponse:
        """
        Get details of a specific voice by voice_id.
        
        Args:
            voice_id: ElevenLabs voice ID
            
        Returns:
            ElevenLabsResponse with voice details
        """
        logger.info(f"Fetching voice: {voice_id}")
        response = self._get(f"/voices/{voice_id}")
        
        if response.status:
            logger.info(f"✅ Voice fetched: {voice_id}")
        else:
            logger.error(f"Failed to fetch voice {voice_id}: {response.error_message}")
        
        return response
    
    def get_all_voices(self) -> ElevenLabsResponse:
        """
        Get all available voices from ElevenLabs account.
        
        Returns:
            ElevenLabsResponse with list of voices
        """
        logger.info("Fetching all voices from ElevenLabs")
        response = self._get("/voices")
        
        if response.status:
            voices = response.data.get("voices", [])
            logger.info(f"✅ Fetched {len(voices)} voices")
        else:
            logger.error(f"Failed to fetch voices: {response.error_message}")
        
        return response
    
    def search_voices(self, 
                     page_size: int = 30,
                     search: Optional[str] = None,
                     voice_type: Optional[str] = None,
                     category: Optional[str] = None,
                     **kwargs) -> ElevenLabsResponse:
        """
        Search and filter voices with advanced options.
        
        Args:
            page_size: Number of results per page (max 100)
            search: Search query string
            voice_type: Filter by voice type ('personal', 'community', 'default', etc.)
            category: Filter by category ('premade', 'cloned', 'generated', 'professional')
            **kwargs: Additional filter parameters
            
        Returns:
            ElevenLabsResponse with filtered voices
        """
        params = {"page_size": min(page_size, 100)}
        
        if search:
            params["search"] = search
        if voice_type:
            params["voice_type"] = voice_type
        if category:
            params["category"] = category
        
        # Add any additional params
        params.update({k: v for k, v in kwargs.items() if v is not None})
        
        logger.info(f"Searching voices with params: {params}")
        response = self._get("/voices", params=params)
        
        if response.status:
            voices = response.data.get("voices", [])
            logger.info(f"✅ Search returned {len(voices)} voices")
        else:
            logger.error(f"Voice search failed: {response.error_message}")
        
        return response
    
    def update_voice(self, voice_id: str, name: Optional[str] = None, 
                    description: Optional[str] = None,
                    labels: Optional[Dict[str, str]] = None) -> ElevenLabsResponse:
        """
        Update voice metadata (name, description, labels).
        
        Args:
            voice_id: ElevenLabs voice ID
            name: New name for the voice
            description: New description
            labels: New metadata labels
            
        Returns:
            ElevenLabsResponse with updated voice data
        """
        # First check if voice exists
        check = self.get_voice(voice_id)
        if not check.status:
            return ElevenLabsResponse(status=False, error_message=f"Voice not found: {voice_id}")
        
        data = {}
        if name:
            data["name"] = name
        if description:
            data["description"] = description
        if labels:
            data["labels"] = labels
        
        if not data:
            return ElevenLabsResponse(status=False, error_message="No update data provided")
        
        logger.info(f"Updating voice {voice_id} with data: {data}")
        response = self._post(f"/voices/{voice_id}/edit", data=data)
        
        if response.status:
            logger.info(f"✅ Voice {voice_id} updated successfully")
        else:
            logger.error(f"Failed to update voice {voice_id}: {response.error_message}")
        
        return response
    
    def delete_voice(self, voice_id: str) -> ElevenLabsResponse:
        """
        Delete a voice from ElevenLabs account.
        
        Args:
            voice_id: ElevenLabs voice ID to delete
            
        Returns:
            ElevenLabsResponse indicating success or failure
        """
        # First check if voice exists
        check = self.get_voice(voice_id)
        if not check.status:
            return ElevenLabsResponse(status=False, error_message=f"Voice not found: {voice_id}")
        
        logger.info(f"Deleting voice: {voice_id}")
        response = self._delete(f"/voices/{voice_id}")
        
        if response.status:
            logger.info(f"✅ Voice {voice_id} deleted successfully")
        else:
            logger.error(f"Failed to delete voice {voice_id}: {response.error_message}")
        
        return response
    
    def get_voice_settings(self, voice_id: str) -> ElevenLabsResponse:
        """
        Get default voice settings for a voice.
        
        Args:
            voice_id: ElevenLabs voice ID
            
        Returns:
            ElevenLabsResponse with voice settings (stability, similarity_boost, etc.)
        """
        logger.info(f"Fetching settings for voice: {voice_id}")
        response = self._get(f"/voices/{voice_id}/settings")
        
        if response.status:
            logger.info(f"✅ Voice settings fetched for {voice_id}")
        else:
            logger.error(f"Failed to fetch settings for {voice_id}: {response.error_message}")
        
        return response
    
    def update_voice_settings(self, voice_id: str, 
                            stability: Optional[float] = None,
                            similarity_boost: Optional[float] = None,
                            style: Optional[float] = None,
                            use_speaker_boost: Optional[bool] = None) -> ElevenLabsResponse:
        """
        Update voice settings.
        
        Args:
            voice_id: ElevenLabs voice ID
            stability: Stability setting (0.0 to 1.0)
            similarity_boost: Similarity boost (0.0 to 1.0)
            style: Style exaggeration (0.0 to 1.0)
            use_speaker_boost: Whether to use speaker boost
            
        Returns:
            ElevenLabsResponse with updated settings
        """
        data = {}
        if stability is not None:
            data["stability"] = max(0.0, min(1.0, stability))
        if similarity_boost is not None:
            data["similarity_boost"] = max(0.0, min(1.0, similarity_boost))
        if style is not None:
            data["style"] = max(0.0, min(1.0, style))
        if use_speaker_boost is not None:
            data["use_speaker_boost"] = use_speaker_boost
        
        if not data:
            return ElevenLabsResponse(status=False, error_message="No settings provided")
        
        logger.info(f"Updating settings for voice {voice_id}: {data}")
        response = self._post(f"/voices/{voice_id}/settings/edit", data=data)
        
        if response.status:
            logger.info(f"✅ Voice settings updated for {voice_id}")
        else:
            logger.error(f"Failed to update settings for {voice_id}: {response.error_message}")
        
        return response

    # def get_voice_samples(self, voice_id: str) -> ElevenLabsResponse:
    #     """
    #     Fetch the first available audio sample for a given ElevenLabs voice.

    #     Args:
    #         voice_id (str): ElevenLabs voice ID

    #     Returns:
    #         ElevenLabsResponse: Response containing voice sample data or error info
    #     """
    #     logger.info(f"🎙️ Fetching samples for voice: {voice_id}")

    #     # Step 1: Fetch voice metadata
    #     voice_response = self._get(f"/voices/{voice_id}")
    #     if not voice_response.status:
    #         logger.error(
    #             f"❌ Failed to fetch voice details for {voice_id}: "
    #             f"{voice_response.error_message}"
    #         )
    #         return voice_response

    #     samples = voice_response.data.get("samples", [])
    #     if not samples:
    #         logger.warning(f"⚠️ No samples found for voice: {voice_id}")
    #         return ElevenLabsResponse(
    #             status=False,
    #             data=None,
    #             error_message="No samples available for this voice"
    #         )

    #     # Step 2: Fetch the first valid sample
    #     sample_id = samples[0].get("sample_id")
    #     if not sample_id:
    #         logger.error(f"❌ Sample ID missing for voice: {voice_id}")
    #         return ElevenLabsResponse(
    #             status=False,
    #             data=None,
    #             error_message="Invalid sample metadata"
    #         )

    #     sample_response = self._get(f"/voices/{voice_id}/samples/{sample_id}/audio", raw=True)
    #     if sample_response.status:
    #         logger.info(f"✅ Voice sample fetched for {voice_id} (sample_id={sample_id})")
    #     else:
    #         logger.error(
    #             f"❌ Failed to fetch sample {sample_id} for {voice_id}: "
    #             f"{sample_response.error_message}"
    #         )

    #     return sample_response
