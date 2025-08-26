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