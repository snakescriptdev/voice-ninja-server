from fastapi import APIRouter,Request
from .schemas.format import (
    ErrorResponse, 
    SuccessResponse, 
    AudioFileResponse, 
    AudioFileListResponse
)
from app.core import logger
from app.services import AudioStorage
from starlette.responses import JSONResponse, FileResponse
router = APIRouter(prefix="/api")


@router.get("/heartbeat/")
async def heartbeat():
    logger.info("Heartbeat endpoint called")
    return JSONResponse(content={"message": "Voice Agent is running and ready to receive calls"})




@router.get(
    "/audio/{session_id}/",
    responses={
        200: {"model": AudioFileResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def get_audio_file(session_id: str):
    """Get audio file for a session"""
    try:
        audio_file = AudioStorage.get_audio_path(session_id)
        if not audio_file:
            return JSONResponse(
                status_code=404,
                content=ErrorResponse(error="Audio file not found").dict()
            )
        
        return FileResponse(
            path=audio_file,
            media_type="audio/wav",
            filename=audio_file.name,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'attachment; filename="{audio_file.name}"'
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error retrieving audio file: {str(e)}").dict()
        )


@router.get(
    "/audio-files/",
    response_model=AudioFileListResponse,
    responses={
        500: {"model": ErrorResponse}
    }
)
async def get_audio_file_list(request: Request):
    try:
        audio_files = AudioStorage.get_audio_files(request)
        return AudioFileListResponse(
            audio_files=[
                AudioFileResponse(
                    filename=file.name,
                    session_id=file.session_id,
                    file_url=file.url,
                    created_at=file.created_at,
                    voice=file.voice,
                    duration=file.duration
                ) for file in audio_files
            ]
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error retrieving audio files: {str(e)}").dict()
        )

@router.delete(
    "/audio-delete/{session_id}/",
    response_model=SuccessResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def delete_audio_file(session_id: str):
    """Delete audio file for session"""
    try:
        if AudioStorage.delete_audio(session_id):
            return SuccessResponse(message="Audio file deleted successfully")
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="Audio file not found").dict()
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error deleting audio file: {str(e)}").dict()
        )

