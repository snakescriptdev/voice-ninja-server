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
from app.databases.models import AudioRecordings
import hmac
import hashlib
import google.generativeai as genai
import aiohttp
from aiohttp import ClientConnectorError
import base64

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




def generate_agent_prompt(agent_function, agent_tone, level_of_detail, industry, agent_name=None):
    """
    Generates a tailored AI agent prompt based on user selections using Gemini.
    
    Parameters:
    - agent_function: The primary function/role of the agent (e.g., 'customer_support', 'sales', 'technical_advisor')
    - agent_tone: The conversational tone of the agent (e.g., 'professional', 'friendly', 'technical')
    - level_of_detail: How detailed the agent responses should be (e.g., 'concise', 'moderate', 'comprehensive')
    - industry: The industry the agent will operate in (e.g., 'healthcare', 'finance', 'retail')
    - agent_name: Optional name for the agent (defaults to a any name if None)
    
    Returns:
    - The prompt generated by Gemini based on the user's selections
    """

    
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    model = genai.GenerativeModel("gemini-2.0-flash-exp")
    
    gemini_instruction = f"""
    Create a detailed and effective AI agent prompt based on the following specifications:
    
    AGENT SPECIFICATIONS:
    - Name: {agent_name}
    - Primary function: {agent_function}
    - Communication tone: {agent_tone}
    - Level of detail in responses: {level_of_detail}
    - Industry specialization: {industry}
    
    The prompt should:
    1. Begin with a clear definition of the agent's identity and role
    2. Include specific instructions on how the agent should communicate based on the specified tone
    3. Provide guidance on the appropriate level of detail for responses
    4. Include industry-specific knowledge, terminology, and best practices
    5. Outline any limitations or boundaries for the agent
    6. Include instructions for handling uncertainty or questions outside its scope
    
    Format the prompt as a comprehensive instruction set that could be directly used with an LLM.
    ** IMPORTANT **: Return only the prompt text, formatted cleanly with appropriate sections and structure.
    """
    
    try:
        result = model.generate_content(gemini_instruction)
        return result.text
    
    except Exception as e:
        fallback_prompt = f"""You are an AI agent named {agent_name} specializing in {industry}.

        Your primary function is to provide {agent_function} assistance. 
        You should maintain a {agent_tone} tone in your communications.
        When responding to queries, provide {level_of_detail} level of detail.

        Always introduce yourself as {agent_name} at the beginning of conversations.
        If you don't know something, acknowledge it rather than providing incorrect information.
        """
        return fallback_prompt
    
async def send_request(url, data):
    """Sends an async HTTP request with domain validation."""
    if not url:
        logger.error("Invalid URL: URL is None or empty")
        return {"status": "error", "message": "Invalid URL"}
    
    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        try:
            logger.info(f"Sending request to {url} with data: {data}")
            async with session.post(url, json=data, headers=headers) as response:
                if response.status == 404:
                    return {"status": "error", "message": "Domain not found (404)"}
                elif response.status >= 500:
                    return {"status": "error", "message": "Server error, try again later"}
                
                return await response.json()
        
        except ClientConnectorError:
            logger.error(f"Domain not found or unreachable: {url}")
            return {"status": "error", "message": "Domain not found or unreachable"}
        
        except Exception as e:
            logger.exception(f"Error sending request: {e}")
            return {"status": "error", "message": f"Request failed: {str(e)}"}


def generate_transcript(audio_file_path):
    """
    Generates transcript from an audio file using Google's Gemini Flash 2.0 model.
    
    Args:
        audio_file_path (str): Path to the audio file
        
    Returns:
        str: Transcribed text from the audio
    """
    audio_file_path = os.path.abspath(audio_file_path)
    try:
        # Initialize Gemini model
        genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        
        # Load audio file and convert to base64
        with open(audio_file_path, 'rb') as audio:
            audio_data = audio.read()
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            
        # Create content dict with audio data
        response = model.generate_content(
        contents=[
                    {
                        "parts": [
                            {
                                "inline_data": {
                                    "mime_type": "audio/wav",
                                    "data": audio_base64,  # Ensure the audio is Base64-encoded
                                }
                            }
                        ]
                    }
                ]   
            )
        
        # Extract raw transcript text
        raw_transcript = response.text.strip()

        # Request structured output
        prompt = f"""
        You are a transcription assistant. The following is a raw transcript of a conversation:
        
        {raw_transcript}
        
        Please format this conversation into a structured JSON list where:
        - User messages are marked as "user".
        - Bot messages are marked as "bot".
        - Messages should alternate between user and bot.
        
        Output must be valid JSON format without any extra text.
        """

        
        structured_response = model.generate_content(prompt,generation_config={"response_mime_type": "application/json"})

        chat_transcript = json.loads(structured_response.text)
        
        return chat_transcript, raw_transcript
        
    except Exception as e:
        logger.exception(f"Error generating transcript: {e}")
        return f"Failed to generate transcript: {str(e)}", None



def generate_summary(audio_file_path):
    """
    Generates a summary from an audio file by first transcribing it and then summarizing the transcript.
    
    Args:
        audio_file_path (str): Path to the audio file
        
    Returns:
        dict: Dictionary containing status and either summary or error message
    """
    try:
        # First generate transcript from audio
        transcript, raw_transcript = generate_transcript(audio_file_path)
            
        # Initialize Gemini model for summarization
        genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
        model = genai.GenerativeModel('gemini-1.5-pro-latest')

        
        # Prompt for summarization
        prompt = f"""Please provide a concise summary of the following transcript:
        
        {raw_transcript}
        
        Focus on the key points and main ideas. Keep the summary clear and brief."""
        
        # Generate summary
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 500
            }
        )
        
        summary = response.text.strip()
        
        return summary
        
    except Exception as e:
        logger.exception(f"Error generating summary: {e}")
        return {
            "status": "error", 
            "message": f"Failed to generate summary: {str(e)}"
        }
