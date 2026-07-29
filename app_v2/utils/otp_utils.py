import random
import string
import re
from datetime import timedelta
from app_v2.core.logger import setup_logger
from app_v2.utils.email_service import send_email_async
from app_v2.utils.email_templates import render_email, heading, paragraph, signoff

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
        content = (
            heading("Your Verification Code")
            + paragraph("Use this code to complete your login:")
            + f'<div style="background:#f9fafb;border:1px solid #e5e7eb;padding:18px;text-align:center;'
              f'font-size:32px;font-weight:800;letter-spacing:6px;margin:0 0 20px;border-radius:8px;'
              f'color:#111827;">{otp}</div>'
            + paragraph("This code expires in 10 minutes.", muted=True)
            + signoff("If you didn't request this code, you can safely ignore this email.")
        )
        html = render_email(content, preheader=f"Your verification code is {otp}")
        plain_text = (
            "Your Verification Code\n\n"
            f"Use this code to complete your login: {otp}\n\n"
            "This code expires in 10 minutes.\n\n"
            "If you didn't request this code, you can safely ignore this email.\n\n"
            "Thanks,\nVoice Ninja Team"
        )
        await send_email_async("Your Login Code", [email], html, plain_text_body=plain_text)
        logger.info(f"Email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False
