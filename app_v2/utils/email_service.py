from io import BytesIO

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from starlette.datastructures import Headers, UploadFile
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


async def send_email_async(
    subject: str,
    recipients: list,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
):
    """
    attachments: list of (filename, content_bytes, mime_type), e.g.
    [("invoice.pdf", pdf_bytes, "application/pdf")].
    """
    upload_attachments = []
    for filename, content, mime_type in (attachments or []):
        upload_attachments.append(
            UploadFile(
                file=BytesIO(content),
                filename=filename,
                headers=Headers({"content-type": mime_type}),
            )
        )

    message = MessageSchema(
        subject=subject,
        recipients=recipients,
        body=body,
        subtype="html",
        attachments=upload_attachments,
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


async def send_insufficient_call_balance_email(
    user_email: str,
    current_balance: int,
    minimum_credits_per_minute: int,
    minutes_available: float,
    base_url: str,
    user_name: str | None = None,
):
    """
    Sent right after a call ends when the user's remaining balance has
    dropped below the minimum coins required to start even one more call
    (minimum_credits_per_minute x minimum_call_minutes), so future calls
    will be blocked until they recharge.
    """
    try:
        subject = "🚫 Insufficient Coins — Calls Are Now Blocked"
        recharge_link = f"{base_url}/billing-wallet"
        minutes_display = f"{minutes_available:.1f}".rstrip("0").rstrip(".") or "0"

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Insufficient Coin Balance 🚫</h2>

                    <p>Hi {user_name or "User"},</p>

                    <p>
                    Your call just ended, and your remaining coin balance is no longer
                    enough to start a new call.
                    </p>

                    <div style="padding:18px 20px;background:#fdecea;border-radius:8px;border-left:5px solid #e53935;margin:20px 0;">
                        <p style="margin:0 0 8px 0;"><b>No new calls can be made right now.</b></p>
                        <p style="margin:0;">
                        A call requires at least <b>{minimum_credits_per_minute:,} coins per minute</b>,
                        but you currently have coins for only <b>~{minutes_display} minute(s)</b>.
                        </p>
                    </div>

                    <table width="100%" cellpadding="10" style="border-collapse:collapse;margin-bottom:20px;">
                        <tr style="background:#f8f9fa;">
                            <td><b>Your Current Balance</b></td>
                            <td>{current_balance:,} coins</td>
                        </tr>
                        <tr>
                            <td><b>Minimum Required Rate</b></td>
                            <td>{minimum_credits_per_minute:,} coins / minute</td>
                        </tr>
                        <tr style="background:#f8f9fa;">
                            <td><b>Call Time Left</b></td>
                            <td>~{minutes_display} minute(s)</td>
                        </tr>
                    </table>

                    <p>Recharge now to keep making calls without interruption.</p>

                    <a href="{recharge_link}"
                       style="display:inline-block;padding:12px 22px;background:#e53935;color:white;
                              text-decoration:none;border-radius:6px;font-weight:bold;">
                       Recharge Now
                    </a>

                    <br/><br/>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body
        )

    except Exception as e:
        logger.error(f"Failed to send insufficient call balance email: {str(e)}")


async def send_low_agent_balance_email(
    user_email: str,
    agent_name: str,
    current_balance: int,
    credits_per_minute: float,
    base_url: str,
    user_name: str | None = None,
):
    """
    Sent right after a call ends when the user's remaining balance has
    dropped below what this specific agent needs for even one more minute
    of calling (agent.avg_credits_per_minute, a live 1-minute cost
    projection refreshed after every call to this agent) — so their next
    call with THIS agent will likely end well under a minute.
    """
    try:
        subject = f"⚠️ Low Balance for {agent_name} — Calls May End Early"
        recharge_link = f"{base_url}/billing-wallet"
        estimated_seconds = int(max(current_balance, 0) / credits_per_minute * 60)

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Low Balance for {agent_name} ⚠️</h2>

                    <p>Hi {user_name or "User"},</p>

                    <p>
                    Your call with <b>{agent_name}</b> just ended, and your remaining coin
                    balance is now below what this agent needs for even one more minute
                    of calling.
                    </p>

                    <div style="padding:18px 20px;background:#fff3cd;border-radius:8px;border-left:5px solid #ffc107;margin:20px 0;">
                        <p style="margin:0;">
                        Your next call with <b>{agent_name}</b> will last at most
                        <b>~{estimated_seconds} second(s)</b> before it's cut short.
                        </p>
                    </div>

                    <table width="100%" cellpadding="10" style="border-collapse:collapse;margin-bottom:20px;">
                        <tr style="background:#f8f9fa;">
                            <td><b>Your Current Balance</b></td>
                            <td>{current_balance:,} coins</td>
                        </tr>
                        <tr>
                            <td><b>{agent_name} — Cost per Minute</b></td>
                            <td>{credits_per_minute:,.0f} coins / minute</td>
                        </tr>
                    </table>

                    <p>Recharge now to keep making calls with this agent without interruption.</p>

                    <a href="{recharge_link}"
                       style="display:inline-block;padding:12px 22px;background:#ffc107;color:#1f2937;
                              text-decoration:none;border-radius:6px;font-weight:bold;">
                       Recharge Now
                    </a>

                    <br/><br/>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body
        )

    except Exception as e:
        logger.error(f"Failed to send low agent balance email: {str(e)}")


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

async def send_cost_overrun_email(
    recipients: list,
    conversation_id: int,
    agent_name: str,
    actual_conversation: float,
    calculated_conversation: float,
    actual_llm: float,
    calculated_llm: float,
    conversation_overrun_pct: float | None = None,
    llm_overrun_pct: float | None = None,
):
    """
    Alert admins that a conversation's ACTUAL cost exceeded our CALCULATED
    estimate by more than the anomaly threshold (conversation and/or LLM), so
    pricing assumptions can be reviewed. Recipients are passed in (already
    resolved) so this coroutine never touches the DB — safe to dispatch
    fire-and-forget from a finalize call.

    conversation_overrun_pct / llm_overrun_pct: set only for the metric(s)
    that actually crossed the alert threshold, so the row can be flagged —
    the other metric's raw actual/estimated values are still shown for
    context even when it didn't cross the threshold.
    """
    def _fmt(v):
        return f"{v:,.2f}" if v is not None else "—"

    def _fmt_pct(p):
        if p is None:
            return None
        return "∞" if p == float("inf") else f"+{p:,.1f}%"

    conv_pct_str = _fmt_pct(conversation_overrun_pct)
    llm_pct_str = _fmt_pct(llm_overrun_pct)

    def _row(label, actual, calculated, pct_str):
        flag = (
            f' — <b style="color:#c62828;">{pct_str} over, margin loss ⚠️</b>'
            if pct_str
            else ""
        )
        return f"<li>{label} — actual: <b>{_fmt(actual)}</b> cr, estimated: {_fmt(calculated)} cr{flag}</li>"

    subject = f"⚠️ Cost overrun on conversation #{conversation_id}"
    body = f"""
    <h2>⚠️ Conversation Cost Overrun</h2>
    <p>Conversation <b>#{conversation_id}</b> (agent: <b>{agent_name or 'Unknown'}</b>)
    cost significantly more than our estimate on at least one metric — a margin loss worth reviewing.</p>
    <ul>
      {_row("Conversation", actual_conversation, calculated_conversation, conv_pct_str)}
      {_row("LLM", actual_llm, calculated_llm, llm_pct_str)}
    </ul>
    <p>Review it under Admin → ElevenLabs Usage → Conversations.</p>
    """
    await send_email_async(subject=subject, recipients=recipients, body=body)


async def send_payment_success_email(
    user_email: str,
    user_name: str | None,
    amount: float,
    currency: str,
    coins: int,
    provider_payment_id: str | None,
    base_url: str,
    invoice_pdf: bytes | None = None,
):
    """Sent once a credit purchase is confirmed (webhook or client-verify, whichever wins the race)."""
    try:
        subject = "✅ Payment Received — Credits Added"
        billing_link = f"{base_url}/billing-wallet"

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Payment Received ✅</h2>
                    <p>Hi {user_name or "there"},</p>
                    <p>Thanks for your purchase — your coins have been added to your wallet.</p>

                    <table width="100%" cellpadding="10" style="border-collapse:collapse;margin:20px 0;">
                        <tr style="background:#f8f9fa;">
                            <td><b>Amount Paid</b></td>
                            <td>{currency} {amount:,.2f}</td>
                        </tr>
                        <tr>
                            <td><b>Credits Added</b></td>
                            <td>{coins:,} coins</td>
                        </tr>
                        <tr style="background:#f8f9fa;">
                            <td><b>Payment ID</b></td>
                            <td>{provider_payment_id or "-"}</td>
                        </tr>
                    </table>

                    <a href="{billing_link}"
                       style="display:inline-block;padding:12px 22px;background:#22c55e;color:white;
                              text-decoration:none;border-radius:6px;font-weight:bold;">
                       View Billing &amp; Wallet
                    </a>

                    <br/><br/>
                    <p style="color:#555;">
                        {"Your invoice is attached to this email as a PDF." if invoice_pdf else "You can download an invoice for this payment anytime from Billing &amp; Wallet → Billing History."}
                    </p>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        attachments = [("invoice.pdf", invoice_pdf, "application/pdf")] if invoice_pdf else None
        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=attachments,
        )
    except Exception as e:
        logger.error(f"Failed to send payment success email: {str(e)}")


async def send_payment_failed_email(
    user_email: str,
    user_name: str | None,
    amount: float,
    currency: str,
    error_reason: str | None,
    base_url: str,
    invoice_pdf: bytes | None = None,
):
    """Sent when Razorpay reports a payment attempt failed (webhook payment.failed)."""
    try:
        subject = "❌ Payment Failed"
        billing_link = f"{base_url}/billing-wallet"

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Payment Failed ❌</h2>
                    <p>Hi {user_name or "there"},</p>
                    <p>Your attempt to add credits didn't go through, and no coins were added to your wallet.</p>

                    <div style="padding:18px 20px;background:#fdecea;border-radius:8px;border-left:5px solid #e53935;margin:20px 0;">
                        <p style="margin:0 0 8px 0;"><b>Amount:</b> {currency} {amount:,.2f}</p>
                        <p style="margin:0;"><b>Reason:</b> {error_reason or "Payment was not completed"}</p>
                    </div>

                    <p>No charge was made to your account for this attempt. You can try again anytime.</p>

                    <a href="{billing_link}"
                       style="display:inline-block;padding:12px 22px;background:#e53935;color:white;
                              text-decoration:none;border-radius:6px;font-weight:bold;">
                       Try Again
                    </a>

                    <br/><br/>
                    <p style="color:#555;">
                        {"A receipt for this attempt is attached to this email as a PDF." if invoice_pdf else ""}
                    </p>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        attachments = [("invoice.pdf", invoice_pdf, "application/pdf")] if invoice_pdf else None
        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=attachments,
        )
    except Exception as e:
        logger.error(f"Failed to send payment failed email: {str(e)}")


async def send_usage_history_export_email(
    user_email: str,
    user_name: str | None,
    csv_bytes: bytes,
    record_count: int,
):
    """Sent when a user exports their usage history (CSV attached)."""
    try:
        subject = "Your Usage History Export"

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Usage History Export</h2>
                    <p>Hi {user_name or "there"},</p>
                    <p>
                        As requested, here's your usage history export — {record_count}
                        record{"s" if record_count != 1 else ""} — attached to this email as a CSV file.
                    </p>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=[("usage_history.csv", csv_bytes, "text/csv")],
        )
    except Exception as e:
        logger.error(f"Failed to send usage history export email: {str(e)}")


async def send_billing_history_export_email(
    user_email: str,
    user_name: str | None,
    csv_bytes: bytes,
    record_count: int,
):
    """Sent when a user exports their full billing history (CSV attached)."""
    try:
        subject = "Your Billing History Export"

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Billing History Export</h2>
                    <p>Hi {user_name or "there"},</p>
                    <p>
                        As requested, here's your complete billing history — {record_count}
                        record{"s" if record_count != 1 else ""} — attached to this email as a CSV file.
                    </p>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=[("billing_history.csv", csv_bytes, "text/csv")],
        )
    except Exception as e:
        logger.error(f"Failed to send billing history export email: {str(e)}")


async def send_account_suspended_email(
    user_email: str,
    user_name: str | None,
    reason: str | None,
):
    """Sent when an admin suspends a user's account."""
    try:
        subject = "Your Account Has Been Suspended"

        reason_block = f"""
                    <div style="padding:18px 20px;background:#fdecea;border-radius:8px;border-left:5px solid #e53935;margin:20px 0;">
                        <p style="margin:0;"><b>Reason:</b> {reason}</p>
                    </div>
        """ if reason else ""

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Account Suspended</h2>
                    <p>Hi {user_name or "there"},</p>
                    <p>Your Voice Ninja account has been suspended and you will not be able to access your account until it is reactivated.</p>
                    {reason_block}
                    <p>If you have any questions or believe this was a mistake, please contact our support team for more details.</p>

                    <br/>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
        )
    except Exception as e:
        logger.error(f"Failed to send account suspended email: {str(e)}")


async def send_account_reactivated_email(
    user_email: str,
    user_name: str | None,
):
    """Sent when an admin reactivates a previously suspended user's account."""
    try:
        subject = "Your Account Has Been Reactivated"

        body = f"""
        <html>
        <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;margin:0;">
        <table width="600" align="center" style="background:white;border-radius:10px;padding:30px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.08);">
            <tr>
                <td>
                    <h2 style="color:#1f2937;margin-top:0;">Account Reactivated ✅</h2>
                    <p>Hi {user_name or "there"},</p>
                    <p>Good news — your Voice Ninja account is active again and you can resume using the service as normal.</p>

                    <br/>
                    <p style="color:#555;">Thanks,<br/>Voice Ninja Team</p>
                </td>
            </tr>
        </table>
        </body>
        </html>
        """

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
        )
    except Exception as e:
        logger.error(f"Failed to send account reactivated email: {str(e)}")


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