"""
ElevenLabs Conversation Storage Service

This service handles database operations for ElevenLabs conversations,
including call records, audio recordings, and transcript storage.
"""

import os
import json
from datetime import datetime
from typing import Dict, Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from fastapi_sqlalchemy import db
import uuid
import base64
from pathlib import Path
import logging

# Import database models
from app.databases.models import CallModel, AudioRecordings, ConversationModel, AgentModel

logger = logging.getLogger(__name__)


class ElevenLabsConversationStorage:
    """
    Service class for managing ElevenLabs conversation data storage
    Handles call records, audio recordings, and conversation transcripts
    """
    
    def __init__(self, audio_storage_path: str = None):
        # Use the same audio storage path as the main app
        self.audio_storage_path = audio_storage_path or "/Users/apple/Desktop/Voice Ninja/voice_ninja/audio_storage"
        self.elevenlabs_storage_path = Path(self.audio_storage_path) / "elevenlabs"
        self.elevenlabs_storage_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"ElevenLabsConversationStorage initialized with path: {self.elevenlabs_storage_path}")

    def create_call_record(self, agent_id: int, conversation_id: str, user_id: int, session_metadata: Dict = None) -> Optional[CallModel]:
        """
        Create a new call record in the database
        
        Args:
            agent_id: ID of the agent handling the call
            conversation_id: Unique conversation/call identifier
            user_id: ID of the user who owns the agent
            session_metadata: Additional metadata for the session
            
        Returns:
            CallModel instance if successful, None otherwise
        """
        try:
            with db():
                # Check if call record already exists
                existing_call = db.session.query(CallModel).filter(
                    CallModel.call_id == conversation_id
                ).first()
                
                if existing_call:
                    logger.info(f"Call record already exists for conversation_id: {conversation_id}")
                    return existing_call

                # Create new call record
                variables = session_metadata or {}
                variables.update({
                    "user_id": user_id,
                    "status": "active",
                    "created_timestamp": datetime.now().isoformat()
                })
                
                call_record = CallModel(
                    agent_id=agent_id,
                    call_id=conversation_id,
                    variables=variables,
                    created_at=datetime.now()
                )
                
                db.session.add(call_record)
                db.session.commit()
                db.session.refresh(call_record)
                
                logger.info(f"Created call record with ID: {call_record.id} for conversation: {conversation_id}")
                return call_record
                
        except SQLAlchemyError as e:
            logger.error(f"Database error creating call record: {str(e)}")
            if db.session:
                db.session.rollback()
            return None
        except Exception as e:
            logger.error(f"Error creating call record: {str(e)}")
            return None

    def update_call_status(self, conversation_id: str, status: str, end_metadata: Dict = None) -> bool:
        """
        Update the status of an existing call record
        
        Args:
            conversation_id: Unique conversation/call identifier
            status: New status for the call
            end_metadata: Additional metadata for call completion
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with db():
                call_record = db.session.query(CallModel).filter(
                    CallModel.call_id == conversation_id
                ).first()
                
                if not call_record:
                    logger.warning(f"Call record not found for conversation_id: {conversation_id}")
                    return False

                # Update variables with new status and metadata
                variables = call_record.variables or {}
                variables.update({
                    "status": status,
                    "updated_timestamp": datetime.now().isoformat()
                })
                
                if end_metadata:
                    variables.update(end_metadata)
                
                call_record.variables = variables
                db.session.commit()
                
                logger.info(f"Updated call status to '{status}' for conversation: {conversation_id}")
                return True
                
        except SQLAlchemyError as e:
            logger.error(f"Database error updating call status: {str(e)}")
            if db.session:
                db.session.rollback()
            return False
        except Exception as e:
            logger.error(f"Error updating call status: {str(e)}")
            return False

    def store_conversation_transcript(self, audio_recording_id: int, transcript_data: List[Dict], summary: str = "") -> Optional[ConversationModel]:
        """
        Store conversation transcript in the database
        
        Args:
            audio_recording_id: ID of the associated audio recording
            transcript_data: List of transcript entries
            summary: Optional conversation summary
            
        Returns:
            ConversationModel instance if successful, None otherwise
        """
        try:
            with db():
                # Check if transcript already exists
                existing_transcript = db.session.query(ConversationModel).filter(
                    ConversationModel.audio_recording_id == audio_recording_id
                ).first()
                
                if existing_transcript:
                    # Update existing transcript
                    existing_transcript.transcript = transcript_data
                    existing_transcript.summary = summary
                    existing_transcript.updated_at = datetime.now()
                    db.session.commit()
                    db.session.refresh(existing_transcript)
                    logger.info(f"Updated existing transcript for audio_recording_id: {audio_recording_id}")
                    return existing_transcript
                else:
                    # Create new transcript
                    conversation = ConversationModel(
                        audio_recording_id=audio_recording_id,
                        transcript=transcript_data,
                        summary=summary,
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    
                    db.session.add(conversation)
                    db.session.commit()
                    db.session.refresh(conversation)
                    
                    logger.info(f"Created new transcript for audio_recording_id: {audio_recording_id}")
                    return conversation
                    
        except SQLAlchemyError as e:
            logger.error(f"Database error storing transcript: {str(e)}")
            if db.session:
                db.session.rollback()
            return None
        except Exception as e:
            logger.error(f"Error storing conversation transcript: {str(e)}")
            return None

    def get_call_record_by_conversation_id(self, conversation_id: str) -> Optional[CallModel]:
        """
        Get call record by conversation ID
        
        Args:
            conversation_id: Unique conversation/call identifier
            
        Returns:
            CallModel instance if found, None otherwise
        """
        try:
            with db():
                return db.session.query(CallModel).filter(
                    CallModel.call_id == conversation_id
                ).first()
        except Exception as e:
            logger.error(f"Error getting call record: {str(e)}")
            return None

    def get_audio_recordings_by_agent(self, agent_id: int) -> List[AudioRecordings]:
        """
        Get all audio recordings for a specific agent
        
        Args:
            agent_id: ID of the agent
            
        Returns:
            List of AudioRecordings instances
        """
        try:
            with db():
                return db.session.query(AudioRecordings).filter(
                    AudioRecordings.agent_id == agent_id
                ).order_by(AudioRecordings.created_at.desc()).all()
        except Exception as e:
            logger.error(f"Error getting audio recordings: {str(e)}")
            return []

    def get_call_history_by_user(self, user_id: int, limit: int = 50) -> List[Dict]:
        """
        Get call history for a specific user
        
        Args:
            user_id: ID of the user
            limit: Maximum number of records to return
            
        Returns:
            List of call history dictionaries
        """
        try:
            with db():
                # Get calls for agents owned by the user
                calls = db.session.query(CallModel).join(AgentModel).filter(
                    AgentModel.created_by == user_id
                ).order_by(CallModel.created_at.desc()).limit(limit).all()
                
                call_history = []
                for call in calls:
                    # Get associated audio recordings
                    audio_recordings = db.session.query(AudioRecordings).filter(
                        AudioRecordings.call_id == call.call_id
                    ).all()
                    
                    # Get conversation transcripts
                    transcripts = []
                    for recording in audio_recordings:
                        conversation = db.session.query(ConversationModel).filter(
                            ConversationModel.audio_recording_id == recording.id
                        ).first()
                        if conversation:
                            transcripts.append({
                                "transcript": conversation.transcript,
                                "summary": conversation.summary
                            })
                    
                    call_data = {
                        "id": call.id,
                        "call_id": call.call_id,
                        "agent_id": call.agent_id,
                        "agent_name": call.agent.agent_name if call.agent else "Unknown",
                        "created_at": call.created_at.isoformat(),
                        "variables": call.variables,
                        "audio_recordings": len(audio_recordings),
                        "has_transcript": len(transcripts) > 0,
                        "transcripts": transcripts
                    }
                    call_history.append(call_data)
                
                return call_history
                
        except Exception as e:
            logger.error(f"Error getting call history: {str(e)}")
            return []

    def delete_call_record(self, conversation_id: str) -> bool:
        """
        Delete a call record and associated data
        
        Args:
            conversation_id: Unique conversation/call identifier
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with db():
                # Get call record
                call_record = db.session.query(CallModel).filter(
                    CallModel.call_id == conversation_id
                ).first()
                
                if not call_record:
                    logger.warning(f"Call record not found for conversation_id: {conversation_id}")
                    return False

                # Delete associated audio recordings and files
                audio_recordings = db.session.query(AudioRecordings).filter(
                    AudioRecordings.call_id == conversation_id
                ).all()
                
                for recording in audio_recordings:
                    # Delete conversation transcripts
                    conversations = db.session.query(ConversationModel).filter(
                        ConversationModel.audio_recording_id == recording.id
                    ).all()
                    for conversation in conversations:
                        db.session.delete(conversation)
                    
                    # Delete audio file
                    try:
                        if os.path.exists(recording.audio_file):
                            os.remove(recording.audio_file)
                    except Exception as e:
                        logger.warning(f"Could not delete audio file {recording.audio_file}: {str(e)}")
                    
                    # Delete audio recording record
                    db.session.delete(recording)
                
                # Delete call record
                db.session.delete(call_record)
                db.session.commit()
                
                logger.info(f"Deleted call record and associated data for conversation: {conversation_id}")
                return True
                
        except SQLAlchemyError as e:
            logger.error(f"Database error deleting call record: {str(e)}")
            if db.session:
                db.session.rollback()
            return False
        except Exception as e:
            logger.error(f"Error deleting call record: {str(e)}")
            return False


# Create global instance for use across the application
elevenlabs_conversation_storage = ElevenLabsConversationStorage()
