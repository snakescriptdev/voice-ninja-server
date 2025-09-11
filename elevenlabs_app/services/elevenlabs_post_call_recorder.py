import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, List, Any
from pathlib import Path
from dataclasses import dataclass
from loguru import logger
from elevenlabs import ElevenLabs
import time


@dataclass
class ElevenLabsCallRecord:
    """Data structure for ElevenLabs conversation records retrieved from their API"""
    conversation_id: str
    agent_dynamic_id: str
    elevenlabs_agent_id: str
    call_type: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    conversation_data: Optional[Dict] = None
    audio_file_path: Optional[str] = None
    transcript_file_path: Optional[str] = None
    metadata_file_path: Optional[str] = None
    retrieval_status: str = "pending"  # pending, completed, failed
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.conversation_data is None:
            self.conversation_data = {}


class ElevenLabsPostCallRecorder:
    """
    Retrieves call recordings from ElevenLabs API after conversations end
    This replaces the local capture system with ElevenLabs' native recording retrieval
    """
    
    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or "/Users/apple/Desktop/Voice Ninja/voice_ninja/audio_storage"
        self.recordings_path = Path(self.storage_path) / "elevenlabs_api_recordings"
        self.recordings_path.mkdir(parents=True, exist_ok=True)
        
        # ElevenLabs client
        self.client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
        
        # Track conversation sessions that need post-call retrieval
        self.pending_retrievals: Dict[str, ElevenLabsCallRecord] = {}
        self.completed_recordings: Dict[str, ElevenLabsCallRecord] = {}
        
        # Background task for post-call retrieval
        self.retrieval_task = None
        self.running = False
        
        logger.info(f"ElevenLabsPostCallRecorder initialized with storage: {self.recordings_path}")

    async def start_retrieval_service(self):
        """Start the background service for post-call data retrieval"""
        if self.running:
            logger.warning("Retrieval service already running")
            return
        
        self.running = True
        self.retrieval_task = asyncio.create_task(self._retrieval_loop())
        logger.info("📡 ElevenLabs post-call retrieval service started")

    async def stop_retrieval_service(self):
        """Stop the background retrieval service"""
        self.running = False
        if self.retrieval_task:
            self.retrieval_task.cancel()
            try:
                await self.retrieval_task
            except asyncio.CancelledError:
                pass
        logger.info("📡 ElevenLabs post-call retrieval service stopped")

    def register_conversation_session(self, 
                                    call_id: str, 
                                    agent_dynamic_id: str, 
                                    elevenlabs_agent_id: str,
                                    call_type: str = "browser_live",
                                    metadata: Dict = None) -> bool:
        """
        Register a conversation session for post-call retrieval
        This is called when a conversation STARTS, not during the call
        """
        try:
            call_record = ElevenLabsCallRecord(
                conversation_id=call_id,  # We'll update this with actual ElevenLabs conversation ID later
                agent_dynamic_id=agent_dynamic_id,
                elevenlabs_agent_id=elevenlabs_agent_id,
                call_type=call_type,
                start_time=datetime.now(timezone.utc)
            )
            
            self.pending_retrievals[call_id] = call_record
            logger.info(f"📝 Registered session for post-call retrieval: {call_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to register session {call_id}: {e}")
            return False

    def mark_conversation_ended(self, call_id: str, elevenlabs_conversation_id: str = None) -> bool:
        """
        Mark a conversation as ended and ready for retrieval
        This triggers the post-call data retrieval process
        """
        try:
            if call_id not in self.pending_retrievals:
                logger.warning(f"Session {call_id} not found in pending retrievals")
                return False
            
            call_record = self.pending_retrievals[call_id]
            call_record.end_time = datetime.now(timezone.utc)
            
            if elevenlabs_conversation_id:
                call_record.conversation_id = elevenlabs_conversation_id
            
            # Calculate duration
            if call_record.start_time and call_record.end_time:
                duration = call_record.end_time - call_record.start_time
                call_record.duration_seconds = duration.total_seconds()
            
            logger.info(f"🔚 Marked conversation as ended: {call_id} (EL ID: {elevenlabs_conversation_id})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to mark conversation ended {call_id}: {e}")
            return False

    async def _retrieval_loop(self):
        """Background loop that checks for completed conversations and retrieves data"""
        while self.running:
            try:
                # Check for conversations ready for retrieval
                ready_for_retrieval = []
                
                for call_id, record in self.pending_retrievals.items():
                    if record.end_time and record.retrieval_status == "pending":
                        # Wait a bit after conversation ends for ElevenLabs to process
                        time_since_end = datetime.now(timezone.utc) - record.end_time
                        if time_since_end.total_seconds() > 30:  # Wait 30 seconds after end
                            ready_for_retrieval.append(call_id)
                
                # Process retrievals
                for call_id in ready_for_retrieval:
                    await self._retrieve_conversation_data(call_id)
                
                # Wait before next check
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in retrieval loop: {e}")
                await asyncio.sleep(30)  # Wait longer on error

    async def _retrieve_conversation_data(self, call_id: str):
        """Retrieve conversation data from ElevenLabs API"""
        try:
            record = self.pending_retrievals[call_id]
            record.retrieval_status = "processing"
            
            logger.info(f"📥 Starting data retrieval for conversation: {call_id}")
            
            # If we don't have the ElevenLabs conversation ID, try to find it
            if not record.conversation_id or record.conversation_id == call_id:
                elevenlabs_conversation_id = await self._find_conversation_by_agent_and_time(
                    record.elevenlabs_agent_id, 
                    record.start_time,
                    record.end_time
                )
                if elevenlabs_conversation_id:
                    record.conversation_id = elevenlabs_conversation_id
                else:
                    logger.warning(f"Could not find ElevenLabs conversation ID for {call_id}")
                    record.retrieval_status = "failed"
                    record.error_message = "ElevenLabs conversation ID not found"
                    return
            
            # Retrieve conversation details from ElevenLabs
            conversation_data = await self._get_conversation_details(record.conversation_id)
            if not conversation_data:
                record.retrieval_status = "failed"
                record.error_message = "Failed to retrieve conversation details"
                return
            
            record.conversation_data = conversation_data
            
            # Create export directory
            timestamp = record.start_time.strftime("%Y%m%d_%H%M%S")
            export_dir = self.recordings_path / f"{call_id}_{timestamp}"
            export_dir.mkdir(exist_ok=True)
            
            # Save conversation metadata
            metadata_file = export_dir / "conversation_metadata.json"
            await self._save_conversation_metadata(record, metadata_file)
            record.metadata_file_path = str(metadata_file)
            
            # Save transcript
            transcript_file = export_dir / "transcript.json"
            await self._save_transcript(conversation_data, transcript_file)
            record.transcript_file_path = str(transcript_file)
            
            # Download and save audio if available
            if conversation_data.get("has_audio"):
                audio_file = export_dir / "conversation_audio.wav"
                success = await self._download_conversation_audio(record.conversation_id, audio_file)
                if success:
                    record.audio_file_path = str(audio_file)
            
            # Mark as completed
            record.retrieval_status = "completed"
            self.completed_recordings[call_id] = record
            del self.pending_retrievals[call_id]
            
            logger.info(f"✅ Successfully retrieved conversation data: {call_id}")
            
        except Exception as e:
            logger.error(f"Failed to retrieve conversation data for {call_id}: {e}")
            if call_id in self.pending_retrievals:
                self.pending_retrievals[call_id].retrieval_status = "failed"
                self.pending_retrievals[call_id].error_message = str(e)

    async def _find_conversation_by_agent_and_time(self, 
                                                 elevenlabs_agent_id: str, 
                                                 start_time: datetime, 
                                                 end_time: datetime) -> Optional[str]:
        """Find ElevenLabs conversation ID by agent and time range"""
        try:
            # Convert to unix timestamps
            start_unix = int(start_time.timestamp())
            end_unix = int(end_time.timestamp()) if end_time else int(datetime.now(timezone.utc).timestamp())
            
            # Get recent conversations for this agent
            conversations = self.client.conversational_ai.conversations.list(
                agent_id=elevenlabs_agent_id,
                page_size=20  # Check last 20 conversations
            )
            
            # Find conversation that matches our time range
            for conv in conversations.conversations:
                conv_start = conv.start_time_unix_secs if hasattr(conv, 'start_time_unix_secs') else 0
                
                # Check if conversation started within our time range (±5 minutes buffer)
                time_diff = abs(conv_start - start_unix)
                if time_diff <= 300:  # 5 minutes buffer
                    logger.info(f"🔍 Found matching conversation: {conv.conversation_id}")
                    return conv.conversation_id
            
            logger.warning(f"No matching conversation found for agent {elevenlabs_agent_id} in time range")
            return None
            
        except Exception as e:
            logger.error(f"Error finding conversation by agent and time: {e}")
            return None

    async def _get_conversation_details(self, conversation_id: str) -> Optional[Dict]:
        """Get detailed conversation data from ElevenLabs"""
        try:
            conversation = self.client.conversational_ai.conversations.get(conversation_id)
            
            # Process transcript messages
            transcript_messages = []
            if conversation.transcript:
                for msg in conversation.transcript:
                    message_data = {
                        "role": msg.role,
                        "message": msg.message,
                        "time_in_call_secs": msg.time_in_call_secs,
                        "source_medium": msg.source_medium,
                        "interrupted": msg.interrupted
                    }
                    
                    # Add metrics if available
                    if hasattr(msg, 'conversation_turn_metrics') and msg.conversation_turn_metrics:
                        metrics = {}
                        for metric_name, metric_record in msg.conversation_turn_metrics.metrics.items():
                            metrics[metric_name] = metric_record.elapsed_time
                        message_data["metrics"] = metrics
                    
                    transcript_messages.append(message_data)
            
            # Process analysis
            analysis_data = {}
            if conversation.analysis:
                analysis_data = {
                    "call_successful": conversation.analysis.call_successful,
                    "transcript_summary": conversation.analysis.transcript_summary,
                    "call_summary_title": conversation.analysis.call_summary_title,
                    "evaluation_criteria_results": conversation.analysis.evaluation_criteria_results,
                    "data_collection_results": conversation.analysis.data_collection_results
                }
            
            # Process metadata
            metadata_data = {}
            if conversation.metadata:
                metadata_data = {
                    "start_time_unix_secs": conversation.metadata.start_time_unix_secs,
                    "call_duration_secs": conversation.metadata.call_duration_secs,
                    "cost": conversation.metadata.cost,
                    "termination_reason": conversation.metadata.termination_reason,
                    "main_language": conversation.metadata.main_language,
                    "text_only": conversation.metadata.text_only
                }
            
            return {
                "conversation_id": conversation.conversation_id,
                "user_id": conversation.user_id,
                "agent_id": conversation.agent_id,
                "status": conversation.status,
                "has_audio": conversation.has_audio,
                "has_user_audio": conversation.has_user_audio,
                "has_response_audio": conversation.has_response_audio,
                "transcript": transcript_messages,
                "analysis": analysis_data,
                "metadata": metadata_data
            }
            
        except Exception as e:
            logger.error(f"Error getting conversation details for {conversation_id}: {e}")
            return None

    async def _download_conversation_audio(self, conversation_id: str, save_path: Path) -> bool:
        """Download conversation audio from ElevenLabs"""
        try:
            logger.info(f"📥 Downloading audio for conversation: {conversation_id}")
            
            audio_stream = self.client.conversational_ai.conversations.audio.get(conversation_id)
            
            audio_chunks = []
            total_bytes = 0
            
            for chunk in audio_stream:
                audio_chunks.append(chunk)
                total_bytes += len(chunk)
            
            # Save audio file
            complete_audio = b''.join(audio_chunks)
            with open(save_path, 'wb') as f:
                f.write(complete_audio)
            
            logger.info(f"✅ Downloaded {total_bytes} bytes of audio to {save_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error downloading audio for {conversation_id}: {e}")
            return False

    async def _save_conversation_metadata(self, record: ElevenLabsCallRecord, file_path: Path):
        """Save conversation metadata to JSON file"""
        try:
            metadata = {
                "call_id": record.conversation_id,
                "agent_dynamic_id": record.agent_dynamic_id,
                "elevenlabs_agent_id": record.elevenlabs_agent_id,
                "elevenlabs_conversation_id": record.conversation_id,
                "call_type": record.call_type,
                "start_time": record.start_time.isoformat(),
                "end_time": record.end_time.isoformat() if record.end_time else None,
                "duration_seconds": record.duration_seconds,
                "retrieval_status": record.retrieval_status,
                "audio_file": record.audio_file_path,
                "transcript_file": record.transcript_file_path,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "conversation_data": record.conversation_data
            }
            
            with open(file_path, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
            
            logger.info(f"💾 Saved metadata to {file_path}")
            
        except Exception as e:
            logger.error(f"Error saving metadata: {e}")

    async def _save_transcript(self, conversation_data: Dict, file_path: Path):
        """Save formatted transcript to JSON file"""
        try:
            transcript_data = {
                "conversation_id": conversation_data.get("conversation_id"),
                "transcript": conversation_data.get("transcript", []),
                "analysis": conversation_data.get("analysis", {}),
                "metadata": conversation_data.get("metadata", {}),
                "message_count": len(conversation_data.get("transcript", [])),
                "exported_at": datetime.now(timezone.utc).isoformat()
            }
            
            with open(file_path, 'w') as f:
                json.dump(transcript_data, f, indent=2, default=str)
            
            logger.info(f"📝 Saved transcript to {file_path}")
            
        except Exception as e:
            logger.error(f"Error saving transcript: {e}")

    # Public API methods
    def get_completed_recordings(self) -> List[Dict]:
        """Get list of all completed recordings"""
        recordings = []
        for record in self.completed_recordings.values():
            recordings.append({
                "call_id": record.conversation_id,
                "agent_dynamic_id": record.agent_dynamic_id,
                "elevenlabs_conversation_id": record.conversation_id,
                "call_type": record.call_type,
                "start_time": record.start_time.isoformat(),
                "end_time": record.end_time.isoformat() if record.end_time else None,
                "duration_seconds": record.duration_seconds,
                "status": record.retrieval_status,
                "has_audio": bool(record.audio_file_path),
                "has_transcript": bool(record.transcript_file_path),
                "audio_file": record.audio_file_path,
                "transcript_file": record.transcript_file_path,
                "metadata_file": record.metadata_file_path
            })
        
        # Sort by start time (newest first)
        recordings.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        return recordings

    def get_pending_retrievals(self) -> List[Dict]:
        """Get list of pending retrievals"""
        pending = []
        for record in self.pending_retrievals.values():
            pending.append({
                "call_id": record.conversation_id,
                "agent_dynamic_id": record.agent_dynamic_id,
                "call_type": record.call_type,
                "start_time": record.start_time.isoformat(),
                "end_time": record.end_time.isoformat() if record.end_time else None,
                "status": record.retrieval_status,
                "error": record.error_message
            })
        
        return pending

    def get_recording_by_id(self, call_id: str) -> Optional[Dict]:
        """Get specific recording by call ID"""
        if call_id in self.completed_recordings:
            record = self.completed_recordings[call_id]
            return {
                "call_id": record.conversation_id,
                "agent_dynamic_id": record.agent_dynamic_id,
                "elevenlabs_conversation_id": record.conversation_id,
                "conversation_data": record.conversation_data,
                "audio_file": record.audio_file_path,
                "transcript_file": record.transcript_file_path,
                "metadata_file": record.metadata_file_path,
                "status": record.retrieval_status
            }
        return None


# Global recorder instance
elevenlabs_post_call_recorder = ElevenLabsPostCallRecorder()
