from datetime import datetime
from dataclasses import dataclass
from fastapi.responses import RedirectResponse
from fastapi import Request
from datetime import datetime
from functools import wraps
import os
from twilio.rest import Client
import fitz, io
from PIL import Image
import pytesseract
import docx2txt
import io,json
import wave
import aiofiles
import logging
from datetime import datetime
from pathlib import Path
from config import MEDIA_DIR
from app.databases.models import AudioRecordings, ConversationModel
import hmac
import hashlib
import google.generativeai as genai
import aiohttp
from aiohttp import ClientConnectorError
import base64
from typing import Optional
from fastapi_sqlalchemy import db
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

class Paginator:
    def __init__(self, items, page, per_page, start, end):
        self.items = items[start:end] 
        self.page = page
        self.per_page = per_page
        self.total = len(items)
        
    @property
    def pages(self):
        return (self.total + self.per_page - 1) // self.per_page
        
    @property
    def has_previous(self):
        return self.page > 1
        
    @property
    def has_next(self):
        return self.page < self.pages
        
    @property
    def previous_page_number(self):
        return max(1, self.page - 1)
        
    @property
    def next_page_number(self):
        return min(self.pages, self.page + 1)
        
    @property
    def page_range(self):
        return range(1, self.pages + 1)

def check_session_expiry_redirect(func):
    
    @wraps(func)  # Preserve function metadata
    async def wrapper(request: Request, *args, **kwargs):
        session_data = request.session.get("user")
        if not session_data:
            return RedirectResponse(url="/login")

        expiry = session_data.get("expiry")
        if not expiry:
            return RedirectResponse(url="/login")

        current_time = datetime.now().timestamp()
        session_created = session_data.get("created_at", current_time - expiry)
        
        if current_time > (session_created + expiry):
            request.session.clear()
            return RedirectResponse(url="/login")
        return await func(request, *args, **kwargs)
    return wrapper

def get_logged_in_user(request: Request):
    user = request.session.get("user")
    if not user or not user.get("is_authenticated"):
        return None
    return user


async def save_audio(audio: bytes, sample_rate: int, num_channels: int, SID: str, voice: str, agent_id: int):
    if not audio:
        return None

    try:
        # Define file paths
        audio_name = f"{SID}.wav"
        relative_path = f"audio_recordings/{audio_name}"  
        full_file_path = Path(MEDIA_DIR) / relative_path  

        full_file_path.parent.mkdir(parents=True, exist_ok=True)

        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2)  
                wf.setnchannels(num_channels)  
                wf.setframerate(sample_rate)  
                wf.writeframes(audio)  

            async with aiofiles.open(full_file_path, "wb") as file:
                await file.write(buffer.getvalue())

        logger.info(f"Audio saved to {full_file_path}")

        AudioRecordings.create(
            agent_id=agent_id,
            audio_file=str(full_file_path),
            audio_name=audio_name,
            created_at=datetime.now(),
            call_id=SID
        )

        return relative_path

    except Exception as e:
        logger.error(f"Error saving audio: {e}")
        return None

def update_system_variables(system_variables_data, agent):
    # Determine timezone safely
    system_timezone = agent.agent_timezone or "UTC"

    # Current UTC time ISO
    current_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).isoformat()

    # Current time in user's timezone
    try:
        current_local_time = datetime.now(ZoneInfo(system_timezone)).isoformat()
    except Exception:
        # fallback to UTC if timezone invalid
        current_local_time = datetime.now(ZoneInfo("UTC")).isoformat()

    # Update values
    for item in system_variables_data:
        name = item["name"]

        if name == "system__current_agent_id":
            item["value"] = agent.elvn_lab_agent_id

        elif name == "system__timezone":
            item["value"] = system_timezone

        elif name == "system__time":
            item["value"] = current_local_time

        elif name == "system__time_utc":
            item["value"] = current_utc

    return system_variables_data
