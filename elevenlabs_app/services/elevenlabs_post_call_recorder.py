import os
import json
import asyncio
import requests
from datetime import datetime, timezone
from typing import Dict, Optional, List, Any
from pathlib import Path
from dataclasses import dataclass
from loguru import logger
from elevenlabs import ElevenLabs
import time

from elevenlabs_app.services.conversation_storage import elevenlabs_conversation_storage


@dataclass
class ElevenLabsCallRecord:
    """Data structure for ElevenLabs conversation records retrieved from their API"""
    conversation_id: str
    agent_dynamic_id: str
    elevenlabs_agent_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    conversation_data: Optional[Dict] = None
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
        if storage_path is None:
            # Use absolute path from current working directory
            import os
            self.storage_path = Path(os.getcwd()) / "audio_storage"
        else:
            self.storage_path = Path(storage_path)
        self.recordings_path = Path(self.storage_path) / "elevenlabs_conversations"  # Changed from elevenlabs_api_recordings
        self.recordings_path.mkdir(parents=True, exist_ok=True)
        
        # ElevenLabs client
        self.client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
        
        # Track conversation sessions that need post-call retrieval
        self.pending_retrievals: Dict[str, ElevenLabsCallRecord] = {}
        self.completed_recordings: Dict[str, ElevenLabsCallRecord] = {}
        
        # Background task for post-call retrieval
        self.retrieval_task = None
        self.running = False
        
        # logger.info(f"ElevenLabsPostCallRecorder initialized with storage: {self.recordings_path}")

    async def start_retrieval_service(self):
        """Start the background service for post-call data retrieval"""
        if self.running:
            logger.warning("Retrieval service already running")
            return
        
        self.running = True
        self.retrieval_task = asyncio.create_task(self._retrieval_loop())
        # logger.info("📡 ElevenLabs post-call retrieval service started")

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
                    
                    # Update the database with the ElevenLabs conversation ID
                    try:
                        from app.databases.models import CallModel
                        call_record = CallModel.get_by_call_id(call_id)
                        if call_record:
                            variables = call_record.variables or {}
                            variables["elevenlabs_conversation_id"] = elevenlabs_conversation_id
                            CallModel.update(call_record.id, variables=variables)
                            logger.info(f"📝 Updated database with ElevenLabs conversation ID: {elevenlabs_conversation_id}")
                    except Exception as e:
                        logger.error(f"❌ Failed to update database with conversation ID: {e}")
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
            
            # Store conversation data in database
            from app.databases.models import CallModel, ConversationModel, AudioRecordings
            logger.info(f"🔍 Looking for call record with call_id: {call_id}")
            
            call_record = CallModel.get_by_call_id(call_id)
            if call_record:
                logger.info(f"✅ Found call record: {call_record.id} for call_id: {call_id}")
                
                # Extract essential information from conversation data
                start_time = None
                end_time = None
                duration = 0
                
                # Extract timing information
                if conversation_data.get("metadata"):
                    metadata = conversation_data["metadata"]
                    start_unix = metadata.get("start_time_unix_secs")
                    duration = metadata.get("call_duration_secs", 0)
                    
                    if start_unix:
                        start_time = datetime.fromtimestamp(start_unix, tz=timezone.utc).isoformat()
                        end_time = datetime.fromtimestamp(start_unix + duration, tz=timezone.utc).isoformat()
                
                # Extract summary from analysis
                summary = ""
                if conversation_data.get("analysis"):
                    analysis = conversation_data["analysis"]
                    summary = analysis.get("transcript_summary", "") or analysis.get("call_summary_title", "")
                
                # Update call record with essential information only (no full conversation_data)
                variables = call_record.variables or {}
                variables.update({
                    "status": "completed",
                    "has_audio": conversation_data.get("has_audio", False),
                    "call_duration_secs": duration,
                    "retrieved_at": datetime.now(timezone.utc).isoformat()
                })
                
                # Add timing information if available
                if start_time:
                    variables["Start Time"] = start_time
                if end_time:
                    variables["End Time"] = end_time
                    
                # Note: Conversation ID will be set later after creating the ConversationModel record
                # CallModel.update will be called after setting the Conversation ID
                
                # Download and store audio if available
                audio_file_path = ""
                if conversation_data.get("has_audio"):
                    audio_file_path = await self._download_conversation_audio(record.conversation_id, call_id)
                
                # Create AudioRecordings record for call history display
                audio_record = AudioRecordings.create(
                    agent_id=call_record.agent_id,
                    audio_file=audio_file_path,  # Use downloaded audio file path
                    audio_name=f"ElevenLabs Call {call_id}",
                    created_at=call_record.created_at,
                    call_id=call_id
                )
                
                # Store transcript in ConversationModel table for proper organization
                if conversation_data.get("transcript"):
                    transcript_data = self._format_transcript_for_db(conversation_data["transcript"])
                    
                    # Extract summary from analysis section (already extracted above)
                    # Use the summary variable that was extracted earlier
                    
                    # Use conversation storage service to store transcript
                    conversation = elevenlabs_conversation_storage.store_conversation_transcript(
                        audio_recording_id=audio_record.id,  # Use audio_record.id as audio_recording_id
                        transcript_data=transcript_data,
                        summary=summary  # Use the summary extracted from analysis
                    )
                    
                    # Update variables with the database conversation ID instead of ElevenLabs ID
                    if conversation:
                        variables["Conversation ID"] = conversation.id  # Use database conversation ID
                        # Update the call record with the conversation ID
                        CallModel.update(call_record.id, variables=variables)
                        logger.info(f"💾 Stored transcript in ConversationModel for call_id: {call_id}, conversation_id: {conversation.id}")
                    else:
                        logger.error(f"❌ Failed to store transcript for call_id: {call_id}")
                else:
                    # If no transcript, still store the conversation ID as the call record ID
                    variables["Conversation ID"] = call_record.id
                    # Update the call record with the variables
                    CallModel.update(call_record.id, variables=variables)
                    
                    logger.info(f"💾 No transcript available for call_id: {call_id}, updated call record")
                
                logger.info(f"💾 Updated call record with conversation data for call_id: {call_id}")
            else:
                logger.warning(f"⚠️ Could not find call record for call_id: {call_id}")

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
        """Get detailed conversation data from ElevenLabs using REST API"""
        try:
            import requests
            
            logger.info(f"📥 Retrieving conversation details for: {conversation_id}")
            
            # Use ElevenLabs REST API to get conversation details
            url = f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}"
            headers = {
                "xi-api-key": os.getenv("ELEVENLABS_API_KEY")
            }
            
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                conversation_data = response.json()
                logger.info(f"✅ Retrieved conversation details for: {conversation_id}")
                
                # Format the transcript for our database
                formatted_transcript = []

                if conversation_data.get("transcript"):
                    for index, msg in enumerate(conversation_data["transcript"]):

                        # Base formatted message dict
                        formatted_message = {
                            "role": msg.get("role", "unknown"),
                            "message": msg.get("message", ""),
                            "time_in_call_secs": msg.get("time_in_call_secs", 0),
                            "source_medium": msg.get("source_medium"),
                            "interrupted": msg.get("interrupted", False),
                            "llm_usage": msg.get("llm_usage"),
                            "tool_calls": msg.get("tool_calls", []),
                            "conversation_turn_metrics": msg.get("conversation_turn_metrics", {}),
                        }

                        # If a tool was called
                        if msg.get("tool_calls"):
                            tool_calls = msg.get("tool_calls")
                            tool_name = msg.get("tool_name")
                            tool_type = msg.get("tool_type")

                            # Get tool results from the next transcript message (if exists)
                            tool_results = None
                            if index + 1 < len(conversation_data["transcript"]):
                                tool_results = conversation_data["transcript"][index + 1].get("tool_results")

                            formatted_message["tool_info"] = {
                                "tool_name": tool_name,
                                "tool_type": tool_type,
                                "tool_results": tool_results,
                            }

                        formatted_transcript.append(formatted_message)


                allowed_system_keys = {
                    "system__time_utc",
                    "system__time",
                    "system__timezone",
                }


                dynamic_variables = conversation_data.get("conversation_initiation_client_data", {}).get("dynamic_variables", {})
                cleaned_dynamic_variables = {
                    k: v
                    for k, v in dynamic_variables.items()
                    if not k.startswith("system__") or k in allowed_system_keys
                }

                error_details_obj = {
                    "termination_reason" : conversation_data.get("metadata",{}).get("termination_reason"),
                }
                conversation_config_override = conversation_data.get("conversation_initiation_client_data",{}).get("conversation_config_override")
                
                # Return the full conversation data with formatted transcript
                return {
                    "conversation_id": conversation_data.get("conversation_id"),
                    "agent_id": conversation_data.get("agent_id"),
                    "user_id": conversation_data.get("user_id"),
                    "status": conversation_data.get("status"),
                    "has_audio": conversation_data.get("has_audio", False),
                    "has_user_audio": conversation_data.get("has_user_audio", False),
                    "has_response_audio": conversation_data.get("has_response_audio", False),
                    "transcript": formatted_transcript,
                    "metadata": conversation_data.get("metadata", {}),
                    "analysis": conversation_data.get("analysis", {}),
                    "conversation_initiation_client_data": conversation_data.get("conversation_initiation_client_data", {}),
                    "raw_data": conversation_data , # Keep original data for reference,
                    "error_details" : error_details_obj,
                    "dynamic_variables": cleaned_dynamic_variables,
                    "conversation_config_override": conversation_config_override
                }
            else:
                logger.error(f"Failed to get conversation details: HTTP {response.status_code} - {response.text}")
                return None
            
        except Exception as e:
            logger.error(f"Error getting conversation details for {conversation_id}: {e}")
            return None

    async def _download_conversation_audio(self, elevenlabs_conversation_id: str, call_id: str) -> str:
        """Download conversation audio from ElevenLabs and save to local storage"""
        try:
            logger.info(f"📥 Downloading audio for conversation: {elevenlabs_conversation_id}")
            
            # Generate unique filename for this call
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"elevenlabs_{call_id}_{timestamp}.wav"
            file_path = self.recordings_path / filename
            
            # Download audio using ElevenLabs API
            url = f"https://api.elevenlabs.io/v1/convai/conversations/{elevenlabs_conversation_id}/audio"
            headers = {
                "xi-api-key": os.getenv("ELEVENLABS_API_KEY")
            }
            
            response = requests.get(url, headers=headers, stream=True)
            
            if response.status_code == 200:
                # Save audio file
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                # Return relative path for web access (relative to audio_storage directory)
                relative_path = f"/audio/elevenlabs_conversations/{filename}"
                logger.info(f"✅ Downloaded audio file: {relative_path}")
                return relative_path
            else:
                logger.warning(f"Failed to download audio: HTTP {response.status_code} - {response.text}")
                return ""
            
        except Exception as e:
            logger.error(f"Error downloading audio for {elevenlabs_conversation_id}: {e}")
            return ""

    # Public API methods
    def get_completed_recordings(self) -> List[Dict]:
        """Get list of all completed recordings"""
        recordings = []
        for record in self.completed_recordings.values():
            recordings.append({
                "call_id": record.conversation_id,
                "agent_dynamic_id": record.agent_dynamic_id,
                "elevenlabs_conversation_id": record.conversation_id,
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

    def _format_transcript_for_db(self, transcript_data: Any) -> List[Dict[str, Any]]:
        """
        Format ElevenLabs transcript data for database storage
        
        Args:
            transcript_data: Raw transcript data from ElevenLabs API
            
        Returns:
            List of formatted transcript segments
        """
        if not transcript_data:
            return []
        
        formatted_transcript = []
        
        try:
            # Handle the new REST API format
            if isinstance(transcript_data, list):
                for segment in transcript_data:
                    if isinstance(segment, dict):
                        # Extract timestamp - use time_in_call_secs if available
                        timestamp = segment.get("timestamp")
                        if not timestamp and segment.get("time_in_call_secs") is not None:
                            # Convert seconds to ISO timestamp (relative to conversation start)
                            from datetime import datetime, timedelta
                            base_time = datetime.utcnow()  # We could use conversation start time here
                            call_time = base_time + timedelta(seconds=segment.get("time_in_call_secs", 0))
                            timestamp = call_time.isoformat()
                        elif not timestamp:
                            timestamp = datetime.utcnow().isoformat()
                        
                        formatted_segment = {
                            "role": segment.get("role", "user"),  # Use 'role' for frontend compatibility
                            "content": segment.get("message", ""),  # Use 'content' for frontend compatibility
                            "speaker": segment.get("role", "unknown"),  # Keep 'speaker' for backward compatibility
                            "text": segment.get("message", ""),         # Keep 'text' for backward compatibility
                            "timestamp": timestamp,
                            "confidence": 1.0,  # ElevenLabs doesn't provide confidence, set to 1.0
                            "time_in_call_secs": segment.get("time_in_call_secs", 0),
                            "source_medium": segment.get("source_medium"),
                            "interrupted": segment.get("interrupted", False),
                            "metrics": segment.get("conversation_turn_metrics", {}),
                            "llm_usage": segment.get("llm_usage")
                        }
                        formatted_transcript.append(formatted_segment)
                        
            elif isinstance(transcript_data, dict):
                # If it's a single segment
                timestamp = transcript_data.get("timestamp", datetime.utcnow().isoformat())
                if not timestamp and transcript_data.get("time_in_call_secs") is not None:
                    from datetime import datetime, timedelta
                    base_time = datetime.utcnow()
                    call_time = base_time + timedelta(seconds=transcript_data.get("time_in_call_secs", 0))
                    timestamp = call_time.isoformat()
                    
                formatted_segment = {
                    "role": transcript_data.get("role", "user"),  # Use 'role' for frontend compatibility
                    "content": transcript_data.get("message", ""),  # Use 'content' for frontend compatibility
                    "speaker": transcript_data.get("role", "unknown"),  # Keep 'speaker' for backward compatibility
                    "text": transcript_data.get("message", ""),         # Keep 'text' for backward compatibility
                    "timestamp": timestamp,
                    "confidence": 1.0,
                    "time_in_call_secs": transcript_data.get("time_in_call_secs", 0),
                    "source_medium": transcript_data.get("source_medium"),
                    "interrupted": transcript_data.get("interrupted", False),
                    "metrics": transcript_data.get("conversation_turn_metrics", {}),
                    "llm_usage": transcript_data.get("llm_usage")
                }
                formatted_transcript.append(formatted_segment)
                
            elif isinstance(transcript_data, str):
                # If it's just a text string
                formatted_segment = {
                    "role": "user",  # Use 'role' for frontend compatibility
                    "content": transcript_data,  # Use 'content' for frontend compatibility
                    "speaker": "unknown",  # Keep 'speaker' for backward compatibility
                    "text": transcript_data,         # Keep 'text' for backward compatibility
                    "timestamp": datetime.utcnow().isoformat(),
                    "confidence": 1.0,
                    "time_in_call_secs": 0,
                    "source_medium": None,
                    "interrupted": False,
                    "metrics": {},
                    "llm_usage": None
                }
                formatted_transcript.append(formatted_segment)
        
        except Exception as e:
            logger.error(f"❌ Error formatting transcript data: {e}")
            # Return a simple format as fallback
            formatted_transcript = [{
                "role": "user",  # Use 'role' for frontend compatibility
                "content": str(transcript_data),  # Use 'content' for frontend compatibility
                "speaker": "unknown",  # Keep 'speaker' for backward compatibility
                "text": str(transcript_data),         # Keep 'text' for backward compatibility
                "timestamp": datetime.utcnow().isoformat(),
                "confidence": 1.0,
                "time_in_call_secs": 0,
                "source_medium": None,
                "interrupted": False,
                "metrics": {},
                "llm_usage": None
            }]
        
        return formatted_transcript


# Global recorder instance
elevenlabs_post_call_recorder = ElevenLabsPostCallRecorder()
