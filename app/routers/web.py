from fastapi import APIRouter,Request
from fastapi.templating import Jinja2Templates
from app.core import VoiceSettings

router = APIRouter()

templates = Jinja2Templates(directory="templates")

@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        "connect.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES
        }
    )

@router.get("/audio_list/")
async def audio_list(request: Request):
    return templates.TemplateResponse(
        "audio_list.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "enable_filters": False
        }
    )