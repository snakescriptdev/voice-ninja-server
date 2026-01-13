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

def build_elevenlabs_tool_config(form_data: dict) -> dict:
    """
    Convert frontend function_parameters → EXACT ElevenLabs webhook tool schema.
    FULL-REPLACE mode. No auto generation. No merging.
    """

    # ------------------------
    # BASIC FIELDS
    # ------------------------
    tool_name = form_data.get("tool_name")
    tool_description = form_data.get("tool_description")
    api_url = form_data.get("api_url")
    http_method = form_data.get("http_method", "POST").upper()
    response_timeout = int(form_data.get("response_timeout", 20))

    # ------------------------
    # PATH PARAMS SCHEMA
    # ------------------------
    path_params_schema = {}
    for p in form_data.get("path_params", []):
        name = p.get("name")
        if not name:
            continue

        entry = {"type": p.get("type", "string")}

        # if p.get("description"):
        #     entry["description"] = p["description"]

        # if p.get("dynamic_variable"):
        #     entry["dynamic_variable"] = p["dynamic_variable"]

        # if p.get("constant_value") not in [None, ""]:
        #     entry["constant_value"] = p["constant_value"]

        if p.get("value_type") == "dynamic_variable":
            entry["dynamic_variable"] = p.get("dynamic_variable")

        if p.get("value_type") == "constant_value":
            entry["constant_value"] = p.get("constant_value")

        if p.get("value_type") == "llm_prompt":
            if p.get("description"):
                entry["description"] = p["description"]
                

        path_params_schema[name] = entry

    # ------------------------
    # QUERY PARAMS SCHEMA
    # ------------------------
    query_params_properties = {}
    for q in form_data.get("query_params", []):
        name = q.get("name")
        if not name:
            continue

        entry = {"type": q.get("type", "string")}

        if q.get("description"):
            entry["description"] = q["description"]

        if q.get("value_type") == "dynamic_variable":
            entry["dynamic_variable"] = q.get("dynamic_variable")

        if q.get("value_type") == "constant_value":
            entry["constant_value"] = q.get("constant_value")

        if q.get("value_type") == "llm_prompt":
            if q.get("description"):
                entry["description"] = q["description"]
                

        query_params_properties[name] = entry

    query_params_schema = None
    if query_params_properties:
        query_params_schema = {"properties": query_params_properties}

    # ------------------------
    # REQUEST BODY SCHEMA
    # ------------------------
    body_properties = {}
    body_required = []

    for b in form_data.get("request_body_properties", []):
        name = b.get("name")
        if not name:
            continue

        entry = {"type": b.get("type", "string")}

        if b.get("description"):
            entry["description"] = b["description"]

        if b.get("value_type") == "dynamic_variable":
            entry["dynamic_variable"] = b.get("dynamic_variable")

        if b.get("value_type") == "constant_value":
            entry["constant_value"] = b.get("constant_value")

        if b.get("value_type") == "llm_prompt":
            if b.get("description"):
                entry["description"] = b["description"]
                

        body_properties[name] = entry

        if b.get("required"):
            body_required.append(name)

    request_body_schema = None
    if body_properties:
        request_body_schema = {
            "type": "object",
            "description": form_data.get("body_description", "Body"),
            "properties": body_properties,
            "required": body_required
        }

    # ------------------------
    # REQUEST HEADERS
    # ------------------------
    request_headers = {}
    for h in form_data.get("request_headers", []):
        name = h.get("name")
        val = h.get("value")
        if name and val:
            request_headers[name] = val

    # ------------------------
    # DYNAMIC VARIABLES
    # ------------------------
    dyn_vars = {}
    for v in form_data.get("dynamic_variables", []):
        name = v.get("name")
        val = v.get("value")
        if name:
            dyn_vars[name] = val

    # ------------------------
    # ASSIGNMENTS
    # ------------------------
    assignments = []
    for a in form_data.get("assignments", []):
        assignments.append({
            "dynamic_variable": a.get("variable"),
            "value_path": a.get("path"),
            "source": "response"
        })

    # ------------------------
    # FINAL TOOL CONFIG
    # ------------------------
    api_schema = {
        "url": api_url,
        "method": http_method,
        "request_headers": request_headers,
        "auth_connection": form_data.get("auth_connection")
    }

    if path_params_schema:
        api_schema["path_params_schema"] = path_params_schema

    if query_params_schema:
        api_schema["query_params_schema"] = query_params_schema

    if request_body_schema:
        api_schema["request_body_schema"] = request_body_schema

    tool_config = {
        "type": "webhook",
        "name": tool_name,
        "description": tool_description,
        "api_schema": api_schema,
        "response_timeout_secs": response_timeout,
        "dynamic_variables": {
            "dynamic_variable_placeholders": dyn_vars
        },
        "assignments": assignments,
        "disable_interruptions": form_data.get("disable_interruptions", False),
        "force_pre_tool_speech": form_data.get("force_pre_tool_speech", False)
    }

    return tool_config
