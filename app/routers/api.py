from fastapi import APIRouter,Request
from .schemas.format import (
    ErrorResponse, 
    SuccessResponse, 
)
from app.core import logger
from app.services import AudioStorage
from starlette.responses import JSONResponse, FileResponse
from app.databases.models import AudioRecordModel
from app.databases.schema import AudioRecordSchema, AudioRecordListSchema
router = APIRouter(prefix="/api")


@router.get("/heartbeat/")
async def heartbeat():
    logger.info("Heartbeat endpoint called")
    return JSONResponse(content={"message": "Voice Agent is running and ready to receive calls"})



@router.get(
    "/audio-files/",
    responses={
        500: {"model": ErrorResponse}
    }
)
async def get_audio_file_list(request: Request):
    try:
        audio_files = AudioRecordModel.get_recent_records()
        response_data = AudioRecordListSchema(audio_records=audio_files).model_dump(request=request)
        response_data['status'] = "success"
        response_data['message'] = "Audio files retrieved successfully"
        return JSONResponse(
            status_code=200,
            content=response_data
        )
    except Exception as e:
        print(e)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error retrieving audio files: {str(e)}").model_dump()
        )

@router.delete(
    "/audio-delete/{id}/",
    response_model=SuccessResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def delete_audio_file(id: int):
    """Delete audio file for session"""
    try:
        audio_record = AudioRecordModel.get_by_id(id)
        if audio_record:
            AudioStorage.delete_audio(audio_record.file_name)
            audio_record.delete()
            return SuccessResponse(message="Audio file deleted successfully")
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="Audio file not found").model_dump()
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error deleting audio file: {str(e)}").dict()
        )

