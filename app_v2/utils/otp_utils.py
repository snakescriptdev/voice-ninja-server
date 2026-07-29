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
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Your Verification Code</h2>
                    <p>Use this code to complete your login:</p>
                    <div style="background:#f5f5f5;padding:15px;text-align:center;font-size:32px;
                                font-weight:bold;letter-spacing:5px;margin:20px 0;border-radius:6px;">
                        {otp}
                    </div>
                    <p style="color:#666;">This code expires in 10 minutes.</p>
                    <p style="color:#555;">
                        If you didn't request this code, you can safely ignore this email.
                    </p>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """
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
