import random
import string
import re
from datetime import timedelta
from app_v2.core.logger import setup_logger
from app_v2.utils.email_service import send_email_async

logger = setup_logger(__name__)

def generate_otp():
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

def is_email(text: str) -> bool:
    """Check if text is an email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, text) is not None

async def send_otp_email(email: str, otp: str):
    """Send OTP via email"""
    try:
        
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
        await send_email_async("Your Login Code", [email], html)
        logger.info(f"Email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False
