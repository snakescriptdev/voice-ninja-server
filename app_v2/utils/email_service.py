from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime

logger = setup_logger(__name__)

email_config = ConnectionConfig(
    MAIL_USERNAME=VoiceSettings.MAIL_USERNAME,
    MAIL_PASSWORD=VoiceSettings.MAIL_PASSWORD,
    MAIL_FROM=VoiceSettings.MAIL_FROM,
    MAIL_PORT=VoiceSettings.MAIL_PORT,
    MAIL_SERVER=VoiceSettings.MAIL_SERVER,
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True
)


# ================= BASE EMAIL TEMPLATE ================= #

def build_base_email_template(content: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8" />
    </head>
    <body style="margin:0; padding:0; background-color:#0b0f1a; font-family:Arial, sans-serif;">
        
        <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 0;">
            <tr>
                <td align="center">
                    
                    <table width="600" cellpadding="0" cellspacing="0"
                        style="background:#111827; border-radius:16px; padding:30px; color:#e5e7eb;">
                        
                        <!-- Header -->
                        <tr>
                            <td style="font-size:22px; font-weight:bold; padding-bottom:20px;">
                                Voice Ninja ⚡
                            </td>
                        </tr>

                        <!-- Content -->
                        <tr>
                            <td style="font-size:14px; line-height:1.6;">
                                {content}
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding-top:30px; font-size:12px; color:#9ca3af;">
                                © {datetime.utcnow().year} Voice Ninja. All rights reserved.
                            </td>
                        </tr>

                    </table>

                </td>
            </tr>
        </table>

    </body>
    </html>
    """


# ================= CORE EMAIL SENDER ================= #

async def send_email_async(subject: str, recipients: list, body: str):
    message = MessageSchema(
        subject=subject,
        recipients=recipients,
        body=body,
        subtype="html"
    )

    fm = FastMail(email_config)
    await fm.send_message(message)


# ================= CONVERSATION EMAIL ================= #

async def send_conversation_notification_email(
    company_email: str,
    agent_name: str,
    conversation_id: str,
    base_url: str,
    user_name: str | None = None,
    summary: str | None = None,
    occurred_at: datetime | None = None,
):
    try:
        occurred_at = occurred_at or datetime.utcnow()
        conversation_link = f"{base_url}/conversations/{conversation_id}"

        content = f"""
        <h2 style="color:#ffffff;">New Conversation 🚀</h2>

        <p><strong>Agent:</strong> {agent_name}</p>
        <p><strong>User:</strong> {user_name or "Anonymous"}</p>
        <p><strong>Time:</strong> {occurred_at}</p>

        <p style="margin-top:15px;">
            <strong>Summary:</strong><br/>
            {summary or "No summary available"}
        </p>

        <div style="margin-top:25px;">
            <a href="{conversation_link}" 
               style="
               background:linear-gradient(90deg,#ff7a18,#ff3d77);
               color:white;
               padding:12px 20px;
               border-radius:8px;
               text-decoration:none;
               display:inline-block;
               font-weight:bold;">
               View Conversation
            </a>
        </div>
        """

        body = build_base_email_template(content)

        await send_email_async(
            subject=f"New Conversation on {agent_name}",
            recipients=[company_email],
            body=body
        )

    except Exception as e:
        logger.error(f"Failed to send conversation email: {str(e)}")


# ================= LOW COINS EMAIL ================= #

async def send_low_coins_email(
    user_email: str,
    current_coins: int,
    base_url: str,
    user_name: str | None = None,
):
    try:
        recharge_link = f"{base_url}/billing-wallet"

        content = f"""
        <h2 style="color:#ffffff;">Low Balance Alert ⚠️</h2>

        <p>Hi {user_name or "User"},</p>

        <p>Your coin balance is running low.</p>

        <p style="font-size:16px;">
            <strong>Current Coins:</strong> 
            <span style="color:#ff7a18;">{current_coins}</span>
        </p>

        <p style="margin-top:15px;">
            Recharge now to avoid any interruption in your services.
        </p>

        <div style="margin-top:25px;">
            <a href="{recharge_link}" 
               style="
               background:linear-gradient(90deg,#ff9800,#ff3d00);
               color:white;
               padding:12px 20px;
               border-radius:8px;
               text-decoration:none;
               display:inline-block;
               font-weight:bold;">
               Recharge Now
            </a>
        </div>
        """

        body = build_base_email_template(content)

        await send_email_async(
            subject="⚠️ You're Running Low on Coins",
            recipients=[user_email],
            body=body
        )

    except Exception as e:
        logger.error(f"Failed to send low coins email: {str(e)}")