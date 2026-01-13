import random
import string
import re
import os
from datetime import timedelta
from twilio.rest import Client
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

def generate_otp():
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

def is_email(text: str) -> bool:
    """Check if text is an email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, text) is not None

def is_phone(text: str) -> bool:
    """Check if text is a phone number"""
    cleaned = re.sub(r'[\s\-\(\)]', '', text)
    pattern = r'^\+?[1-9]\d{9,14}$'
    return re.match(pattern, cleaned) is not None

def normalize_phone(phone: str) -> str:
    """Remove spaces and formatting from phone"""
    return re.sub(r'[\s\-\(\)]', '', phone)

async def send_otp_email(email: str, otp: str):
    """Send OTP via email"""
    try:
        from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
        
        conf = ConnectionConfig(
            MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
            MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
            MAIL_FROM=os.getenv("MAIL_FROM"),
            MAIL_PORT=587,
            MAIL_SERVER="smtp.gmail.com",
            MAIL_STARTTLS=True,
            MAIL_SSL_TLS=False,
            USE_CREDENTIALS=True
        )
        
        html = f"""
        <div style="font-family: Arial; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2>Your Verification Code</h2>
            <p>Use this code to complete your login:</p>
            <div style="background: #f5f5f5; padding: 15px; text-align: center; font-size: 32px; 
                        font-weight: bold; letter-spacing: 5px; margin: 20px 0;">
                {otp}
            </div>
            <p style="color: #666;">This code expires in 10 minutes.</p>
        </div>
        """
        
        message = MessageSchema(
            subject="Your Login Code",
            recipients=[email],
            body=html,
            subtype=MessageType.html
        )
        
        fm = FastMail(conf)
        await fm.send_message(message)
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False

def send_otp_sms(phone: str, otp: str):
    """Send OTP via SMS"""
    try:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone = os.getenv("TWILIO_PHONE_NUMBER")
        
        if not all([account_sid, auth_token, twilio_phone]):
            logger.error("Twilio not configured")
            return False
        
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=f"Your verification code is: {otp}\n\nExpires in 10 minutes.",
            from_=twilio_phone,
            to=phone
        )
        return True
    except Exception as e:
        logger.error(f"SMS send failed: {e}")
        return False
