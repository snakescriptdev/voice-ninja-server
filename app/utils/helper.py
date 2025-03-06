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
import io
import wave
import aiofiles
import logging
from datetime import datetime
from pathlib import Path
from config import MEDIA_DIR
from app.databases.models import AudioRecordings
import hmac
import hashlib

logger = logging.getLogger(__name__)
@dataclass
class AudioFileMetaData:
    SID:str
    voice:str
    created_at:datetime
    audio_type:str

@dataclass
class AudioFile:
    name: str
    url: str
    session_id: str
    voice: str
    created_at: datetime
    duration: float
    


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



def generate_twiml(agent, url, user_id):
    # Convert URL object to string if needed
    url_str = str(url)
    
    if '8000' in url_str:
        base_url = os.environ.get("NGROK_BASE_URL")
        websocket_url = f"{base_url}/ws/"
    else:
        websocket_url = f"{url_str}/ws/"

    twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Connect>
                <Stream url="{websocket_url}">
                <Parameter name="agent_id" value="{agent.id}"/>
                <Parameter name="user_id" value="{user_id}"/>
                </Stream>
            </Connect>
            <Pause length="40"/>
        </Response>
    """
    os.makedirs(f"media/xml_files", exist_ok=True)

    file_path = f"media/xml_files/streams_{agent.id}.xml"
    with open(file_path, "w") as file:
        file.write(twiml_content)
    return file_path



def make_outbound_call(xml):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    client = Client(account_sid, auth_token)


    TO_NUMBER = "+918629049332"  

    call = client.calls.create(
        twiml=open(xml).read(),
        to=TO_NUMBER,
        from_=os.environ.get("TWILIO_PHONE_NUMBER", '+17752648387')
    )
    return call.sid


def extract_text_from_pdf(file_path):
    """Extracts text from a PDF, including OCR for images."""
    full_text = ""
    doc = fitz.open(file_path)

    for page_num, page in enumerate(doc):

        text = page.get_text("text")
        full_text += text + "\n"

        for img_index, img in enumerate(page.get_images(full=True)):
            try:
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]

                image = Image.open(io.BytesIO(image_bytes))

                image_text = pytesseract.image_to_string(image)

                if image_text.strip():
                    full_text += f"\n{image_text}\n"

            except Exception as img_e:
                print(f"Error processing image {img_index + 1} on page {page_num + 1}: {str(img_e)}")

    return full_text.strip() if full_text.strip() else "No readable text found"

def extract_text_from_docx(file_path):
    """Extracts text from a DOCX file."""
    return docx2txt.process(file_path).strip()

def extract_text_from_txt(file_path):
    """Extracts text from a TXT file."""
    with open(file_path, 'r', encoding="utf-8") as file:
        return file.read().strip()

def extract_text_from_file(file_path):
    """Determines file type and extracts text accordingly."""
    if file_path.endswith('.pdf'):
        return extract_text_from_pdf(file_path)
    elif file_path.endswith('.docx'):
        return extract_text_from_docx(file_path)
    elif file_path.endswith('.txt'):
        return extract_text_from_txt(file_path)
    else:
        return "Unsupported file type"


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
            created_at=datetime.now()
        )

        return relative_path

    except Exception as e:
        logger.error(f"Error saving audio: {e}")
        return None
    


def verify_razorpay_signature(order_id, payment_id, signature):
    key_secret = os.getenv("RAZOR_KEY_SECRET")
    
    msg = f"{order_id}|{payment_id}"
    generated_signature = hmac.new(
        key_secret.encode(),
        msg.encode(),
        hashlib.sha256
    ).hexdigest()

    return generated_signature == signature