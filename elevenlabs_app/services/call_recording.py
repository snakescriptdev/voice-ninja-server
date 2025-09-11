import os
import json
import wave
import asyncio
from datetime import datetime
from typing import Dict, Optional, List
from pathlib import Path
import base64
from loguru import logger
from dataclasses import dataclass

@dataclass
class CallRecord:
    """Data structure for storing call recording information"""
    call_id: str
    agent_dynamic_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    user_audio_file: Optional[str] = None
    agent_audio_file: Optional[str] = None
    combined_audio_file: Optional[str] = None
    conversation_transcript: List[Dict] = None
    call_metadata: Dict = None

    def __post_init__(self):
        if self.conversation_transcript is None:
            self.conversation_transcript = []
        if self.call_metadata is None:
            self.call_metadata = {}


class ElevenLabsCallRecorder:
    """
    Handles call recording for ElevenLabs conversations
    Records both user and agent audio streams separately and creates a combined recording
    """
    
    def __init__(self, storage_path: str = None):
        # Use the same audio storage path as the main app
        self.storage_path = storage_path or "/Users/apple/Desktop/Voice Ninja/voice_ninja/audio_storage"
        self.recordings_path = Path(self.storage_path) / "elevenlabs_recordings"
        self.recordings_path.mkdir(parents=True, exist_ok=True)
        
        # Active recordings tracking
        self.active_recordings: Dict[str, CallRecord] = {}
        
        # Audio buffers for active recordings
        self.user_audio_buffers: Dict[str, List[bytes]] = {}
        self.agent_audio_buffers: Dict[str, List[bytes]] = {}
        
        logger.info(f"ElevenLabsCallRecorder initialized with storage path: {self.recordings_path}")

    def start_recording(self, call_id: str, agent_dynamic_id: str, metadata: Dict = None) -> bool:
        """
        Start recording a new call session
        
        Args:
            call_id: Unique identifier for this call
            agent_dynamic_id: The agent's dynamic ID
            metadata: Additional metadata to store with the recording
            
        Returns:
            bool: True if recording started successfully
        """
        try:
            if call_id in self.active_recordings:
                logger.warning(f"Recording already active for call_id: {call_id}")
                return False
            
            # Create call record
            call_record = CallRecord(
                call_id=call_id,
                agent_dynamic_id=agent_dynamic_id,
                start_time=datetime.utcnow(),
                call_metadata=metadata or {}
            )
            
            # Initialize audio buffers
            self.user_audio_buffers[call_id] = []
            self.agent_audio_buffers[call_id] = []
            
            # Store active recording
            self.active_recordings[call_id] = call_record
            
            logger.info(f"Started recording for call_id: {call_id}, agent: {agent_dynamic_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start recording for call_id {call_id}: {e}")
            return False

    def add_user_audio(self, call_id: str, audio_data: bytes) -> bool:
        """
        Add user audio chunk to the recording
        
        Args:
            call_id: The call ID
            audio_data: Raw audio bytes (PCM 16-bit mono 16kHz)
            
        Returns:
            bool: True if audio was added successfully
        """
        try:
            if call_id not in self.active_recordings:
                logger.warning(f"No active recording for call_id: {call_id}")
                return False
            
            self.user_audio_buffers[call_id].append(audio_data)
            return True
            
        except Exception as e:
            logger.error(f"Failed to add user audio for call_id {call_id}: {e}")
            return False

    def add_agent_audio(self, call_id: str, audio_data: bytes) -> bool:
        """
        Add agent audio chunk to the recording
        
        Args:
            call_id: The call ID
            audio_data: Raw audio bytes (PCM 16-bit mono 16kHz)
            
        Returns:
            bool: True if audio was added successfully
        """
        try:
            if call_id not in self.active_recordings:
                logger.warning(f"No active recording for call_id: {call_id}")
                return False
            
            self.agent_audio_buffers[call_id].append(audio_data)
            return True
            
        except Exception as e:
            logger.error(f"Failed to add agent audio for call_id {call_id}: {e}")
            return False

    def add_transcript_message(self, call_id: str, speaker: str, message: str, timestamp: datetime = None) -> bool:
        """
        Add a transcript message to the recording
        
        Args:
            call_id: The call ID
            speaker: 'user' or 'agent'
            message: The transcript text
            timestamp: When the message occurred
            
        Returns:
            bool: True if message was added successfully
        """
        try:
            if call_id not in self.active_recordings:
                logger.warning(f"No active recording for call_id: {call_id}")
                return False
            
            transcript_entry = {
                "speaker": speaker,
                "message": message,
                "timestamp": (timestamp or datetime.utcnow()).isoformat()
            }
            
            self.active_recordings[call_id].conversation_transcript.append(transcript_entry)
            return True
            
        except Exception as e:
            logger.error(f"Failed to add transcript for call_id {call_id}: {e}")
            return False

    def _save_audio_buffer(self, audio_buffer: List[bytes], filename: str) -> Optional[str]:
        """
        Save audio buffer to WAV file
        
        Args:
            audio_buffer: List of audio chunks
            filename: Output filename
            
        Returns:
            str: Full path to saved file, or None if failed
        """
        try:
            if not audio_buffer:
                logger.warning(f"No audio data to save for {filename}")
                return None
            
            filepath = self.recordings_path / filename
            
            # Combine all audio chunks
            combined_audio = b''.join(audio_buffer)
            
            # Save as WAV file (16-bit PCM mono 16kHz)
            with wave.open(str(filepath), 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(16000)  # 16kHz
                wav_file.writeframes(combined_audio)
            
            logger.info(f"Saved audio file: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Failed to save audio file {filename}: {e}")
            return None

    def _create_combined_audio(self, user_file: str, agent_file: str, output_file: str) -> Optional[str]:
        """
        Create a combined audio file with user and agent audio
        This is a simple implementation - for production you might want more sophisticated mixing
        
        Args:
            user_file: Path to user audio file
            agent_file: Path to agent audio file
            output_file: Output filename
            
        Returns:
            str: Path to combined file, or None if failed
        """
        try:
            # For now, we'll just concatenate the files
            # In production, you might want to overlay/mix them properly
            
            output_path = self.recordings_path / output_file
            
            with wave.open(str(output_path), 'wb') as output_wav:
                output_wav.setnchannels(1)
                output_wav.setsampwidth(2)
                output_wav.setframerate(16000)
                
                # Add user audio first
                if os.path.exists(user_file):
                    with wave.open(user_file, 'rb') as user_wav:
                        output_wav.writeframes(user_wav.readframes(user_wav.getnframes()))
                
                # Add agent audio
                if os.path.exists(agent_file):
                    with wave.open(agent_file, 'rb') as agent_wav:
                        output_wav.writeframes(agent_wav.readframes(agent_wav.getnframes()))
            
            logger.info(f"Created combined audio file: {output_path}")
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Failed to create combined audio file: {e}")
            return None

    async def stop_recording(self, call_id: str) -> Optional[CallRecord]:
        """
        Stop recording and save all files
        
        Args:
            call_id: The call ID to stop recording
            
        Returns:
            CallRecord: The completed call record, or None if failed
        """
        try:
            if call_id not in self.active_recordings:
                logger.warning(f"No active recording for call_id: {call_id}")
                return None
            
            call_record = self.active_recordings[call_id]
            call_record.end_time = datetime.utcnow()
            
            # Generate filenames
            timestamp = call_record.start_time.strftime("%Y%m%d_%H%M%S")
            base_filename = f"{call_id}_{timestamp}"
            
            user_filename = f"{base_filename}_user.wav"
            agent_filename = f"{base_filename}_agent.wav"
            combined_filename = f"{base_filename}_combined.wav"
            
            # Save audio files
            user_audio_buffer = self.user_audio_buffers.get(call_id, [])
            agent_audio_buffer = self.agent_audio_buffers.get(call_id, [])
            
            if user_audio_buffer:
                call_record.user_audio_file = self._save_audio_buffer(user_audio_buffer, user_filename)
            
            if agent_audio_buffer:
                call_record.agent_audio_file = self._save_audio_buffer(agent_audio_buffer, agent_filename)
            
            # Create combined audio if both exist
            if call_record.user_audio_file and call_record.agent_audio_file:
                call_record.combined_audio_file = self._create_combined_audio(
                    call_record.user_audio_file,
                    call_record.agent_audio_file,
                    combined_filename
                )
            
            # Save call metadata and transcript
            metadata_filename = f"{base_filename}_metadata.json"
            metadata_path = self.recordings_path / metadata_filename
            
            metadata = {
                "call_id": call_record.call_id,
                "agent_dynamic_id": call_record.agent_dynamic_id,
                "start_time": call_record.start_time.isoformat(),
                "end_time": call_record.end_time.isoformat(),
                "duration_seconds": (call_record.end_time - call_record.start_time).total_seconds(),
                "user_audio_file": call_record.user_audio_file,
                "agent_audio_file": call_record.agent_audio_file,
                "combined_audio_file": call_record.combined_audio_file,
                "conversation_transcript": call_record.conversation_transcript,
                "call_metadata": call_record.call_metadata
            }
            
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Cleanup
            del self.active_recordings[call_id]
            del self.user_audio_buffers[call_id]
            del self.agent_audio_buffers[call_id]
            
            logger.info(f"Recording completed for call_id: {call_id}")
            logger.info(f"Metadata saved to: {metadata_path}")
            
            return call_record
            
        except Exception as e:
            logger.error(f"Failed to stop recording for call_id {call_id}: {e}")
            return None

    def get_recording_info(self, call_id: str) -> Optional[Dict]:
        """
        Get information about an active recording
        
        Args:
            call_id: The call ID
            
        Returns:
            dict: Recording information, or None if not found
        """
        try:
            if call_id not in self.active_recordings:
                return None
            
            call_record = self.active_recordings[call_id]
            return {
                "call_id": call_record.call_id,
                "agent_dynamic_id": call_record.agent_dynamic_id,
                "start_time": call_record.start_time.isoformat(),
                "duration_seconds": (datetime.utcnow() - call_record.start_time).total_seconds(),
                "transcript_messages": len(call_record.conversation_transcript),
                "user_audio_chunks": len(self.user_audio_buffers.get(call_id, [])),
                "agent_audio_chunks": len(self.agent_audio_buffers.get(call_id, []))
            }
            
        except Exception as e:
            logger.error(f"Failed to get recording info for call_id {call_id}: {e}")
            return None

    def list_recordings(self) -> List[Dict]:
        """
        List all completed recordings
        
        Returns:
            list: List of recording metadata
        """
        try:
            recordings = []
            
            for metadata_file in self.recordings_path.glob("*_metadata.json"):
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    recordings.append(metadata)
                except Exception as e:
                    logger.error(f"Failed to read metadata file {metadata_file}: {e}")
            
            # Sort by start time (newest first)
            recordings.sort(key=lambda x: x.get('start_time', ''), reverse=True)
            
            return recordings
            
        except Exception as e:
            logger.error(f"Failed to list recordings: {e}")
            return []

    def get_recording_by_id(self, call_id: str) -> Optional[Dict]:
        """
        Get a specific recording by call ID
        
        Args:
            call_id: The call ID
            
        Returns:
            dict: Recording metadata, or None if not found
        """
        try:
            metadata_files = list(self.recordings_path.glob(f"{call_id}_*_metadata.json"))
            
            if not metadata_files:
                return None
            
            # Take the first match (there should only be one)
            metadata_file = metadata_files[0]
            
            with open(metadata_file, 'r') as f:
                return json.load(f)
            
        except Exception as e:
            logger.error(f"Failed to get recording for call_id {call_id}: {e}")
            return None


# Global recorder instance
elevenlabs_recorder = ElevenLabsCallRecorder()
