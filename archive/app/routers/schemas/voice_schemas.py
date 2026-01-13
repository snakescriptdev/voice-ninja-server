import json
from pydantic import BaseModel, constr, validator, ValidationError
from fastapi.responses import JSONResponse
from typing import Optional

class CreateVoiceSchema(BaseModel):
    voice_name: Optional[constr(strip_whitespace=True, min_length=1, max_length=100)] = None
    audio_file: Optional[str] = None  # filename or path

    @validator("voice_name")
    def voice_name_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Voice name is required and cannot be empty.")
        if len(v) > 100:
            raise ValueError("Voice name must be at most 100 characters.")
        return v.strip()

    @validator("audio_file")
    def audio_file_must_be_string(cls, v):
        if not v or not v.strip():
            raise ValueError("Audio file is required and must be a non-empty string.")
        return v.strip()


class EditVoiceSchema(BaseModel):
    voice_id: Optional[int] = None
    voice_name: Optional[constr(strip_whitespace=True, min_length=1, max_length=100)] = None

    @validator("voice_id")
    def voice_id_required(cls, v):
        if v is None:
            raise ValueError("Voice ID is required.")
        return v

    @validator("voice_name")
    def voice_name_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Voice name is required and cannot be empty.")
        if len(v) > 100:
            raise ValueError("Voice name must be at most 100 characters.")
        return v.strip()


class DeleteVoiceSchema(BaseModel):
    voice_id: Optional[int] = None

    @validator("voice_id")
    def voice_id_required(cls, v):
        if v is None:
            raise ValueError("Voice ID is required.")
        return v

def validate_create_voice_request(voice_name, audio_file):
    try:
        payload = CreateVoiceSchema(
            voice_name=voice_name,
            audio_file=audio_file.filename if audio_file else None
        )
        return payload, None
    except ValidationError as e:
        error = e.errors()[0]
        return None, JSONResponse({
            "status": "error",
            "message": error["msg"].replace("Value error, ", "")
        }, status_code=400)

def validate_edit_voice(data):
    try:
        form_data = {
            "voice_id": data.get("voice_id"),
            "voice_name": data.get("voice_name"),
        }
        EditVoiceSchema(**form_data)
        return None
    except ValidationError as e:
        error = e.errors()[0]
        return JSONResponse(
            {"status": "error", "message": error["msg"].replace("Value error, ", "")},
            status_code=400,
        )


def validate_delete_voice(voice_id):
    try:
        form_data = {"voice_id": voice_id}
        DeleteVoiceSchema(**form_data)
        return None
    except ValidationError as e:
        error = e.errors()[0]
        return JSONResponse(
            {"status": "error", "message": error["msg"].replace("Value error, ", "")},
            status_code=400,
        )
