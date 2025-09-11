from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import List, Dict, Optional
from elevenlabs_app.services.call_recording import elevenlabs_recorder
from loguru import logger
import os
from pathlib import Path

ElevenLabsRecordingRouter = APIRouter(prefix="/elevenlabs/recordings", tags=["elevenlabs-recordings"])

# Setup templates
templates = Jinja2Templates(directory="templates")


@ElevenLabsRecordingRouter.get("/dashboard", response_class=HTMLResponse)
async def recording_dashboard(request: Request):
    """
    Serve the ElevenLabs Recording & Twilio Dashboard
    """
    try:
        return templates.TemplateResponse("elevenlabs_dashboard.html", {"request": request})
    except Exception as e:
        logger.error(f"Error serving dashboard: {e}")
        raise HTTPException(status_code=500, detail="Failed to load dashboard")


@ElevenLabsRecordingRouter.get("/", response_model=List[Dict])
async def list_recordings():
    """
    List all completed call recordings
    
    Returns:
        List[Dict]: List of recording metadata
    """
    try:
        recordings = elevenlabs_recorder.list_recordings()
        return recordings
    except Exception as e:
        logger.error(f"Error listing recordings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ElevenLabsRecordingRouter.get("/{call_id}")
async def get_recording(call_id: str):
    """
    Get detailed information about a specific recording
    
    Args:
        call_id: The call ID
        
    Returns:
        Dict: Recording metadata and details
    """
    try:
        recording = elevenlabs_recorder.get_recording_by_id(call_id)
        if not recording:
            raise HTTPException(status_code=404, detail=f"Recording not found for call_id: {call_id}")
        
        return recording
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting recording {call_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ElevenLabsRecordingRouter.get("/{call_id}/audio/{audio_type}")
async def download_audio_file(call_id: str, audio_type: str):
    """
    Download an audio file for a specific recording
    
    Args:
        call_id: The call ID
        audio_type: Type of audio file ('user', 'agent', or 'combined')
        
    Returns:
        FileResponse: The audio file
    """
    try:
        # Get recording metadata
        recording = elevenlabs_recorder.get_recording_by_id(call_id)
        if not recording:
            raise HTTPException(status_code=404, detail=f"Recording not found for call_id: {call_id}")
        
        # Determine which file to return
        file_path = None
        if audio_type == "user":
            file_path = recording.get("user_audio_file")
        elif audio_type == "agent":
            file_path = recording.get("agent_audio_file")
        elif audio_type == "combined":
            file_path = recording.get("combined_audio_file")
        else:
            raise HTTPException(status_code=400, detail="audio_type must be 'user', 'agent', or 'combined'")
        
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail=f"Audio file not found for type: {audio_type}")
        
        # Return the file
        filename = Path(file_path).name
        return FileResponse(
            path=file_path,
            media_type="audio/wav",
            filename=filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading audio file {call_id}/{audio_type}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ElevenLabsRecordingRouter.get("/{call_id}/transcript")
async def get_transcript(call_id: str):
    """
    Get the conversation transcript for a specific recording
    
    Args:
        call_id: The call ID
        
    Returns:
        Dict: Transcript data
    """
    try:
        recording = elevenlabs_recorder.get_recording_by_id(call_id)
        if not recording:
            raise HTTPException(status_code=404, detail=f"Recording not found for call_id: {call_id}")
        
        transcript = recording.get("conversation_transcript", [])
        
        return {
            "call_id": call_id,
            "transcript": transcript,
            "message_count": len(transcript)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting transcript for {call_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ElevenLabsRecordingRouter.delete("/{call_id}")
async def delete_recording(call_id: str):
    """
    Delete a recording and all its associated files
    
    Args:
        call_id: The call ID
        
    Returns:
        Dict: Deletion result
    """
    try:
        # Get recording metadata first
        recording = elevenlabs_recorder.get_recording_by_id(call_id)
        if not recording:
            raise HTTPException(status_code=404, detail=f"Recording not found for call_id: {call_id}")
        
        deleted_files = []
        
        # Delete audio files
        for audio_type in ["user_audio_file", "agent_audio_file", "combined_audio_file"]:
            file_path = recording.get(audio_type)
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    deleted_files.append(file_path)
                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")
        
        # Delete metadata file
        recordings_path = Path(elevenlabs_recorder.storage_path) / "elevenlabs_recordings"
        metadata_files = list(recordings_path.glob(f"{call_id}_*_metadata.json"))
        
        for metadata_file in metadata_files:
            try:
                os.remove(metadata_file)
                deleted_files.append(str(metadata_file))
            except Exception as e:
                logger.warning(f"Failed to delete metadata file {metadata_file}: {e}")
        
        return {
            "message": f"Recording {call_id} deleted successfully",
            "deleted_files": deleted_files
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting recording {call_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ElevenLabsRecordingRouter.get("/active/{call_id}")
async def get_active_recording_info(call_id: str):
    """
    Get information about an active recording
    
    Args:
        call_id: The call ID
        
    Returns:
        Dict: Active recording information
    """
    try:
        info = elevenlabs_recorder.get_recording_info(call_id)
        if not info:
            raise HTTPException(status_code=404, detail=f"No active recording found for call_id: {call_id}")
        
        return info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting active recording info for {call_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ElevenLabsRecordingRouter.get("/agent/{agent_dynamic_id}")
async def get_recordings_by_agent(agent_dynamic_id: str):
    """
    Get all recordings for a specific agent
    
    Args:
        agent_dynamic_id: The agent's dynamic ID
        
    Returns:
        List[Dict]: List of recordings for the agent
    """
    try:
        all_recordings = elevenlabs_recorder.list_recordings()
        
        # Filter by agent
        agent_recordings = [
            recording for recording in all_recordings 
            if recording.get("agent_dynamic_id") == agent_dynamic_id
        ]
        
        return agent_recordings
        
    except Exception as e:
        logger.error(f"Error getting recordings for agent {agent_dynamic_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@ElevenLabsRecordingRouter.get("/stats/summary")
async def get_recording_stats():
    """
    Get summary statistics about all recordings
    
    Returns:
        Dict: Recording statistics
    """
    try:
        recordings = elevenlabs_recorder.list_recordings()
        
        total_recordings = len(recordings)
        total_duration = sum(r.get("duration_seconds", 0) for r in recordings)
        
        # Group by agent
        agent_stats = {}
        for recording in recordings:
            agent_id = recording.get("agent_dynamic_id", "unknown")
            if agent_id not in agent_stats:
                agent_stats[agent_id] = {
                    "recording_count": 0,
                    "total_duration": 0
                }
            agent_stats[agent_id]["recording_count"] += 1
            agent_stats[agent_id]["total_duration"] += recording.get("duration_seconds", 0)
        
        return {
            "total_recordings": total_recordings,
            "total_duration_seconds": total_duration,
            "total_duration_hours": round(total_duration / 3600, 2),
            "agent_stats": agent_stats
        }
        
    except Exception as e:
        logger.error(f"Error getting recording stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
