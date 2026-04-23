from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timezone

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
        occurred_at = occurred_at or datetime.now(timezone.utc)

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


async def send_coin_expiry_alert_email(
    user_email: str,
    expiring_coins: int,
    expiry_date: str,
    base_url: str,
    user_name: str | None = None,
):
    try:
        subject = "⏳ Your Coins are Expiring Soon!"

        recharge_link = f"{base_url}/billing-wallet"

        body = f"""
        <h2>Coin Expiry Alert ⏳</h2>

        <p>Hi {user_name or "User"},</p>

        <p>This is a friendly reminder that some of your coins will expire soon.</p>

        <p>
        <strong>Expiring Coins:</strong> {expiring_coins} <br/>
        <strong>Expiry Date:</strong> {expiry_date} <br/>
        </p>

        <p>
        Use your coins before they expire, or recharge to extend your usage and keep building.
        </p>

        <br/>

        <a href="{recharge_link}" 
           style="padding:10px 15px; background:#e91e63; color:white; text-decoration:none;">
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
        logger.error(f"Failed to send coin expiry alert email: {str(e)}")


async def send_welcome_subscription_email(user_email: str, unsubscribe_token: str, base_url: str):
    try:
        subject = "Welcome to Voice Ninja! 🚀"
        
        # Ensure the link uses https instead of wss (handle URL object as well as string)
        base_url_str = str(base_url)
        if base_url_str and not base_url_str.endswith("/"):
            base_url_str += "/"
            
        http_base_url = base_url_str.replace("wss://", "https://") if base_url_str else ""
        unsubscribe_link = f"{http_base_url}api/v2/unsubscribe/{unsubscribe_token}"

        body = f"""
        <h2>Thanks for Subscribing! 🚀</h2>

        <p>Hi there,</p>

        <p>Thank you for subscribing to Voice Ninja product updates. We're thrilled to have you with us!</p>

        <p>
        You'll be the first to know about our new features, updates, and exclusive insights.
        </p>

        <p>
        Stay tuned for more exciting news coming your way!
        </p>

        <br/>

        <p>Best regards,<br/>The Voice Ninja Team</p>
        <hr/>
        <p style="font-size: 12px; color: #777;">
            If you didn't mean to subscribe, you can <a href="{unsubscribe_link}">unsubscribe here</a>.
        </p>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body
        )

    except Exception as e:
        logger.error(f"Failed to send welcome subscription email to {user_email}: {str(e)}")

async def send_email_to_admins(db_session, subject: str, html_body: str):
    """
    Sends an email to all users with admin privileges.
    """
    try:
        from app_v2.databases.models import UnifiedAuthModel
        admins = db_session.query(UnifiedAuthModel).filter(UnifiedAuthModel.is_admin == True).all()
        admin_emails = [admin.email for admin in admins if admin.email]
        
        if admin_emails:
            await send_email_async(
                subject=subject,
                recipients=admin_emails,
                body=html_body
            )
    except Exception as e:
        logger.error(f"Failed to send email to admins: {str(e)}")

async def send_voice_limit_email_to_admins(db_session, user_identifier: str, user_id: int):
    """
    Sends an email to all admins notifying them about voice cloning limit reached.
    """
    subject = "Voice Cloning Limit Reached - Action Required"
    message_body = f"""
    <h2>Voice Limit Reached Alert</h2>
    <p>User <b>{user_identifier}</b> (ID: {user_id}) attempted to create a custom cloned voice, but the ElevenLabs limits were reached or the current plan does not support instant voice cloning.</p>
    <p><strong>Please update your ElevenLabs plan so users can continue cloning voices.</strong></p>
    """
    await send_email_to_admins(db_session, subject, message_body)