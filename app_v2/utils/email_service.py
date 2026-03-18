from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
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


async def send_email_async(subject: str, recipients: list, body: str):
    message = MessageSchema(
        subject=subject,
        recipients=recipients,
        body=body,
        subtype="html"
    )

    fm = FastMail(email_config)
    await fm.send_message(message)



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
        # fallback time
        occurred_at = occurred_at or datetime.utcnow()

        # conversation link
        conversation_link = f"{base_url}/conversations/{conversation_id}"

        subject = f"New Conversation on {agent_name}"

        body = f"""
        <h2>New Conversation Alert 🚀</h2>

        <p><strong>Agent:</strong> {agent_name}</p>
        <p><strong>User:</strong> {user_name or "Anonymous"}</p>
        <p><strong>Time:</strong> {occurred_at}</p>

        <p><strong>Summary:</strong><br/>
        {summary or "No summary available"}
        </p>

        <br/>

        <a href="{conversation_link}" 
           style="padding:10px 15px; background:#4CAF50; color:white; text-decoration:none;">
           View Conversation
        </a>
        """

        await send_email_async(
            subject=subject,
            recipients=[company_email],
            body=body
        )

    except Exception as e:
        logger.error(f"Failed to send conversation email: {str(e)}")


async def send_low_coins_email(
    user_email: str,
    current_coins: int,
    base_url: str,
    user_name: str | None = None,
):
    try:
        subject = "⚠️ You're Running Low on Coins"

        recharge_link = f"{base_url}/billing-wallet"

        body = f"""
        <h2>Low Balance Alert ⚠️</h2>

        <p>Hi {user_name or "User"},</p>

        <p>Your coin balance is running low.</p>

        <p>
        <strong>Current Coins:</strong> {current_coins} <br/>
        </p>

        <p>
        To avoid any interruption in your services, please recharge your coins.
        </p>

        <br/>

        <a href="{recharge_link}" 
           style="padding:10px 15px; background:#ff9800; color:white; text-decoration:none;">
           Recharge Now
        </a>

        <br/><br/>

        <p>Thanks,<br/>Voice Ninja Team</p>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body
        )

    except Exception as e:
        logger.error(f"Failed to send low coins email: {str(e)}")