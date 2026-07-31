from io import BytesIO
from pathlib import Path

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType, MultipartSubtypeEnum
from starlette.datastructures import Headers, UploadFile
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timezone

from app_v2.utils.email_templates import (
    render_email,
    heading,
    paragraph,
    button,
    callout,
    info_table,
    signoff,
    LOGO_CID,
)

logger = setup_logger(__name__)

# Attached inline (Content-ID) on every outgoing email so the header logo in
# render_email() (`cid:{LOGO_CID}`) has something to point at — read from
# disk fresh per send (see send_email_async) rather than cached in memory,
# since it's a small static file and this keeps a hot-reload of the asset
# (e.g. a rebranding swap) working without a server restart.
_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "voice_ninja_logo.png"

email_config = ConnectionConfig(
    MAIL_USERNAME=VoiceSettings.MAIL_USERNAME,
    MAIL_PASSWORD=VoiceSettings.MAIL_PASSWORD,
    MAIL_FROM=VoiceSettings.MAIL_FROM,
    MAIL_FROM_NAME=VoiceSettings.MAIL_FROM_NAME,
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
    plain_text_body: str | None = None,
):
    """
    attachments: list of (filename, content_bytes, mime_type), e.g.
    [("invoice.pdf", pdf_bytes, "application/pdf")].

    plain_text_body: when given, sent as a multipart/alternative text/plain
    part alongside the HTML body — an HTML-only email (no plain-text
    alternative) is a known spam-score/deliverability factor.
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

    if _LOGO_PATH.is_file():
        upload_attachments.append({
            "file": str(_LOGO_PATH),
            "mime_type": "image",
            "mime_subtype": "png",
            "headers": {
                "Content-ID": f"<{LOGO_CID}>",
                # Explicit "inline" (not the library's default "attachment")
                # so mail clients render it in the header instead of listing
                # it as a downloadable file.
                "Content-Disposition": "inline",
            },
        })

    # fastapi_mail's attach_alternative() always builds multipart/alternative
    # as [primary `body`/`subtype`, then `alternative_body`] — i.e. whatever
    # is passed as `alternative_body` ends up LAST. Per RFC 2046 §5.1.4,
    # multipart/alternative parts must be ordered least-to-most faithful, and
    # clients render the LAST part they understand — so if HTML were passed
    # as `body` (first/plain-text last), plain-text-capable clients like
    # Gmail render the plain-text part and the HTML design never shows up.
    # Swapping which one is primary vs. alternative puts HTML last so it's
    # the one actually rendered, while still keeping a plain-text part for
    # clients that truly can't do HTML.
    if plain_text_body:
        message = MessageSchema(
            subject=subject,
            recipients=recipients,
            body=plain_text_body,
            subtype="plain",
            attachments=upload_attachments,
            alternative_body=body,
            multipart_subtype=MultipartSubtypeEnum.alternative,
        )
    else:
        message = MessageSchema(
            subject=subject,
            recipients=recipients,
            body=body,
            subtype="html",
            attachments=upload_attachments,
            multipart_subtype=MultipartSubtypeEnum.mixed,
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

        content = (
            heading("New Conversation Alert 🚀")
            + info_table([
                ("Agent", agent_name),
                ("User", user_name or "Anonymous"),
                ("Time", str(occurred_at)),
            ])
            + paragraph(f"<b>Summary</b><br/>{summary or 'No summary available'}")
            + button("View Conversation", conversation_link)
        )
        body = render_email(content, preheader=f"New conversation on {agent_name}")
        plain_text = (
            "New Conversation Alert\n\n"
            f"Agent: {agent_name}\n"
            f"User: {user_name or 'Anonymous'}\n"
            f"Time: {occurred_at}\n\n"
            f"Summary:\n{summary or 'No summary available'}\n\n"
            f"View conversation: {conversation_link}\n"
        )

        await send_email_async(
            subject=subject,
            recipients=[company_email],
            body=body,
            plain_text_body=plain_text,
        )

    except Exception as e:
        logger.error(f"Failed to send conversation email: {str(e)}")


async def send_low_coins_email(
    user_email: str,
    current_balance_inr: float,
    base_url: str,
    user_name: str | None = None,
):
    try:
        subject = "⚠️ You're Running Low on Balance"

        recharge_link = f"{base_url}/billing-wallet"

        content = (
            heading("Low Balance Alert ⚠️")
            + paragraph(f"Hi {user_name or 'User'},")
            + paragraph("Your account balance is running low.")
            + info_table([("Current Balance", f"₹{current_balance_inr:,.2f}")])
            + paragraph("To avoid any interruption in your services, please recharge your balance.")
            + button("Recharge Now", recharge_link, tone="warning")
            + signoff()
        )
        body = render_email(content, preheader="Your account balance is running low")
        plain_text = (
            "Low Balance Alert\n\n"
            f"Hi {user_name or 'User'},\n\n"
            "Your account balance is running low.\n"
            f"Current Balance: ₹{current_balance_inr:,.2f}\n\n"
            "To avoid any interruption in your services, please recharge your balance.\n"
            f"Recharge now: {recharge_link}\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            plain_text_body=plain_text,
        )

    except Exception as e:
        logger.error(f"Failed to send low coins email: {str(e)}")


async def send_insufficient_call_balance_email(
    user_email: str,
    current_balance_inr: float,
    minimum_rate_inr: float,
    minutes_available: float,
    base_url: str,
    user_name: str | None = None,
):
    """
    Sent right after a call ends when the user's remaining balance has
    dropped below the minimum required to start even one more call
    (minimum_credits_per_minute x minimum_call_minutes, converted to INR), so
    future calls will be blocked until they recharge.
    """
    try:
        subject = "🚫 Insufficient Balance — Calls Are Now Blocked"
        recharge_link = f"{base_url}/billing-wallet"
        minutes_display = f"{minutes_available:.1f}".rstrip("0").rstrip(".") or "0"

        content = (
            heading("Insufficient Balance 🚫")
            + paragraph(f"Hi {user_name or 'User'},")
            + paragraph("Your call just ended, and your remaining balance is no longer enough to start a new call.")
            + callout(
                f'<p style="margin:0 0 8px;"><b>No new calls can be made right now.</b></p>'
                f'<p style="margin:0;">A call requires at least <b>₹{minimum_rate_inr:,.2f} per minute</b>, '
                f'but you currently have balance for only <b>~{minutes_display} minute(s)</b>.</p>',
                tone="danger",
            )
            + info_table([
                ("Your Current Balance", f"₹{current_balance_inr:,.2f}"),
                ("Minimum Required Rate", f"₹{minimum_rate_inr:,.2f} / minute"),
                ("Call Time Left", f"~{minutes_display} minute(s)"),
            ])
            + paragraph("Recharge now to keep making calls without interruption.")
            + button("Recharge Now", recharge_link, tone="danger")
            + signoff()
        )
        body = render_email(content, preheader="Your balance is too low to start a new call")
        plain_text = (
            "Insufficient Balance\n\n"
            f"Hi {user_name or 'User'},\n\n"
            "Your call just ended, and your remaining balance is no longer enough to start a new call.\n"
            f"A call requires at least ₹{minimum_rate_inr:,.2f} per minute, but you currently have "
            f"balance for only ~{minutes_display} minute(s).\n\n"
            f"Your Current Balance: ₹{current_balance_inr:,.2f}\n"
            f"Minimum Required Rate: ₹{minimum_rate_inr:,.2f} / minute\n"
            f"Call Time Left: ~{minutes_display} minute(s)\n\n"
            f"Recharge now: {recharge_link}\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            plain_text_body=plain_text,
        )

    except Exception as e:
        logger.error(f"Failed to send insufficient call balance email: {str(e)}")


async def send_low_agent_balance_email(
    user_email: str,
    agent_name: str,
    current_balance_inr: float,
    rate_inr_per_minute: float,
    base_url: str,
    user_name: str | None = None,
):
    """
    Sent right after a call ends when the user's remaining balance has
    dropped below what this specific agent needs for even one more minute
    of calling (agent.avg_credits_per_minute, a live 1-minute cost
    projection refreshed after every call to this agent, converted to INR)
    — so their next call with THIS agent will likely end well under a
    minute.
    """
    try:
        subject = f"⚠️ Low Balance for {agent_name} — Calls May End Early"
        recharge_link = f"{base_url}/billing-wallet"
        estimated_seconds = int(max(current_balance_inr, 0) / rate_inr_per_minute * 60)

        content = (
            heading(f"Low Balance for {agent_name} ⚠️")
            + paragraph(f"Hi {user_name or 'User'},")
            + paragraph(
                f"Your call with <b>{agent_name}</b> just ended, and your remaining balance is now "
                "below what this agent needs for even one more minute of calling."
            )
            + callout(
                f'<p style="margin:0;">Your next call with <b>{agent_name}</b> will last at most '
                f'<b>~{estimated_seconds} second(s)</b> before it\'s cut short.</p>',
                tone="warning",
            )
            + info_table([
                ("Your Current Balance", f"₹{current_balance_inr:,.2f}"),
                (f"{agent_name} — Cost per Minute", f"₹{rate_inr_per_minute:,.2f} / minute"),
            ])
            + paragraph("Recharge now to keep making calls with this agent without interruption.")
            + button("Recharge Now", recharge_link, tone="warning")
            + signoff()
        )
        body = render_email(content, preheader=f"Low balance for {agent_name}")
        plain_text = (
            f"Low Balance for {agent_name}\n\n"
            f"Hi {user_name or 'User'},\n\n"
            f"Your call with {agent_name} just ended, and your remaining balance is now below what "
            "this agent needs for even one more minute of calling.\n"
            f"Your next call with {agent_name} will last at most ~{estimated_seconds} second(s) before it's cut short.\n\n"
            f"Your Current Balance: ₹{current_balance_inr:,.2f}\n"
            f"{agent_name} — Cost per Minute: ₹{rate_inr_per_minute:,.2f} / minute\n\n"
            f"Recharge now: {recharge_link}\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            plain_text_body=plain_text,
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

        content = (
            heading("Thanks for Subscribing! 🚀")
            + paragraph("Hi there,")
            + paragraph("Thank you for subscribing to Voice Ninja product updates. We're thrilled to have you with us!")
            + paragraph("You'll be the first to know about our new features, updates, and exclusive insights.")
            + paragraph("Stay tuned for more exciting news coming your way!")
            + signoff(f'If you didn\'t mean to subscribe, you can <a href="{unsubscribe_link}" style="color:#e06943;">unsubscribe here</a>.')
        )
        body = render_email(content, preheader="Thanks for subscribing to Voice Ninja updates")
        plain_text = (
            "Thanks for Subscribing!\n\n"
            "Hi there,\n\n"
            "Thank you for subscribing to Voice Ninja product updates. We're thrilled to have you with us!\n"
            "You'll be the first to know about our new features, updates, and exclusive insights.\n\n"
            "Stay tuned for more exciting news coming your way!\n\n"
            f"If you didn't mean to subscribe, you can unsubscribe here: {unsubscribe_link}\n\n"
            "Best regards,\nThe Voice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            plain_text_body=plain_text,
        )

    except Exception as e:
        logger.error(f"Failed to send welcome subscription email to {user_email}: {str(e)}")

async def send_email_to_admins(db_session, subject: str, html_body: str, plain_text_body: str | None = None):
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
                body=html_body,
                plain_text_body=plain_text_body,
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

    def _row_html(label, actual, calculated, pct_str):
        flag = f' — <b style="color:#dc2626;">{pct_str} over, margin loss ⚠️</b>' if pct_str else ""
        return f"<li>{label} — actual: <b>{_fmt(actual)}</b> cr, estimated: {_fmt(calculated)} cr{flag}</li>"

    def _row_text(label, actual, calculated, pct_str):
        flag = f" — {pct_str} over, margin loss" if pct_str else ""
        return f"{label} — actual: {_fmt(actual)} cr, estimated: {_fmt(calculated)} cr{flag}"

    subject = f"⚠️ Cost overrun on conversation #{conversation_id}"
    content = (
        heading("⚠️ Conversation Cost Overrun")
        + paragraph(
            f"Conversation <b>#{conversation_id}</b> (agent: <b>{agent_name or 'Unknown'}</b>) "
            "cost significantly more than our estimate on at least one metric — a margin loss worth reviewing."
        )
        + f'<ul style="margin:0 0 20px;padding-left:20px;color:{"#111827"};">'
        + _row_html("Conversation", actual_conversation, calculated_conversation, conv_pct_str)
        + _row_html("LLM", actual_llm, calculated_llm, llm_pct_str)
        + "</ul>"
        + paragraph("Review it under Admin → ElevenLabs Usage → Conversations.", muted=True)
    )
    body = render_email(content, preheader=f"Cost overrun on conversation #{conversation_id}")
    plain_text = (
        "Conversation Cost Overrun\n\n"
        f"Conversation #{conversation_id} (agent: {agent_name or 'Unknown'}) cost significantly more than our "
        "estimate on at least one metric — a margin loss worth reviewing.\n\n"
        f"{_row_text('Conversation', actual_conversation, calculated_conversation, conv_pct_str)}\n"
        f"{_row_text('LLM', actual_llm, calculated_llm, llm_pct_str)}\n\n"
        "Review it under Admin -> ElevenLabs Usage -> Conversations."
    )
    await send_email_async(subject=subject, recipients=recipients, body=body, plain_text_body=plain_text)


async def send_payment_success_email(
    user_email: str,
    user_name: str | None,
    amount: float,
    currency: str,
    provider_payment_id: str | None,
    base_url: str,
    invoice_pdf: bytes | None = None,
):
    """Sent once a credit purchase is confirmed (webhook or client-verify, whichever wins the race)."""
    try:
        subject = "✅ Payment Received"
        billing_link = f"{base_url}/billing-wallet"
        invoice_note = (
            "Your invoice is attached to this email as a PDF."
            if invoice_pdf
            else "You can download an invoice for this payment anytime from Billing &amp; Wallet → Billing History."
        )

        content = (
            heading("Payment Received ✅")
            + paragraph(f"Hi {user_name or 'there'},")
            + paragraph("Thanks for your purchase — your balance has been updated.")
            + info_table([
                ("Amount Paid", f"{currency} {amount:,.2f}"),
                ("Payment ID", provider_payment_id or "-"),
            ])
            + button("View Billing & Wallet", billing_link, tone="success")
            + signoff(invoice_note)
        )
        body = render_email(content, preheader="Your balance has been updated")

        invoice_note_plain = (
            "Your invoice is attached to this email as a PDF."
            if invoice_pdf
            else "You can download an invoice for this payment anytime from Billing & Wallet -> Billing History."
        )
        plain_text = (
            "Payment Received\n\n"
            f"Hi {user_name or 'there'},\n\n"
            "Thanks for your purchase — your balance has been updated.\n\n"
            f"Amount Paid: {currency} {amount:,.2f}\n"
            f"Payment ID: {provider_payment_id or '-'}\n\n"
            f"View Billing & Wallet: {billing_link}\n\n"
            f"{invoice_note_plain}\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        attachments = [("invoice.pdf", invoice_pdf, "application/pdf")] if invoice_pdf else None
        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=attachments,
            plain_text_body=plain_text,
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
        reason = error_reason or "Payment was not completed"

        content = (
            heading("Payment Failed ❌")
            + paragraph(f"Hi {user_name or 'there'},")
            + paragraph("Your payment attempt didn't go through, and your balance was not updated.")
            + callout(
                f'<p style="margin:0 0 8px;"><b>Amount:</b> {currency} {amount:,.2f}</p>'
                f'<p style="margin:0;"><b>Reason:</b> {reason}</p>',
                tone="danger",
            )
            + paragraph("No charge was made to your account for this attempt. You can try again anytime.")
            + button("Try Again", billing_link, tone="danger")
        )
        if invoice_pdf:
            content += signoff("A receipt for this attempt is attached to this email as a PDF.")
        else:
            content += signoff()
        body = render_email(content, preheader="Your payment attempt was not completed")

        plain_text = (
            "Payment Failed\n\n"
            f"Hi {user_name or 'there'},\n\n"
            "Your payment attempt didn't go through, and your balance was not updated.\n\n"
            f"Amount: {currency} {amount:,.2f}\n"
            f"Reason: {reason}\n\n"
            "No charge was made to your account for this attempt. You can try again anytime.\n"
            f"Try again: {billing_link}\n\n"
            + ("A receipt for this attempt is attached to this email as a PDF.\n\n" if invoice_pdf else "")
            + "Thanks,\nVoice Ninja Team"
        )

        attachments = [("invoice.pdf", invoice_pdf, "application/pdf")] if invoice_pdf else None
        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=attachments,
            plain_text_body=plain_text,
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
        record_label = f"{record_count} record{'s' if record_count != 1 else ''}"

        content = (
            heading("Usage History Export")
            + paragraph(f"Hi {user_name or 'there'},")
            + paragraph(f"As requested, here's your usage history export — {record_label} — attached to this email as a CSV file.")
            + signoff()
        )
        body = render_email(content, preheader="Your usage history export is attached")
        plain_text = (
            "Usage History Export\n\n"
            f"Hi {user_name or 'there'},\n\n"
            f"As requested, here's your usage history export — {record_label} — attached to this email as a CSV file.\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=[("usage_history.csv", csv_bytes, "text/csv")],
            plain_text_body=plain_text,
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
        record_label = f"{record_count} record{'s' if record_count != 1 else ''}"

        content = (
            heading("Billing History Export")
            + paragraph(f"Hi {user_name or 'there'},")
            + paragraph(f"As requested, here's your complete billing history — {record_label} — attached to this email as a CSV file.")
            + signoff()
        )
        body = render_email(content, preheader="Your billing history export is attached")
        plain_text = (
            "Billing History Export\n\n"
            f"Hi {user_name or 'there'},\n\n"
            f"As requested, here's your complete billing history — {record_label} — attached to this email as a CSV file.\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            attachments=[("billing_history.csv", csv_bytes, "text/csv")],
            plain_text_body=plain_text,
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

        content = (
            heading("Account Suspended")
            + paragraph(f"Hi {user_name or 'there'},")
            + paragraph("Your Voice Ninja account has been suspended and you will not be able to access your account until it is reactivated.")
        )
        if reason:
            content += callout(f"<p style='margin:0;'><b>Reason:</b> {reason}</p>", tone="danger")
        content += paragraph("If you have any questions or believe this was a mistake, please contact our support team for more details.")
        content += signoff()
        body = render_email(content, preheader="Your Voice Ninja account has been suspended")

        plain_text = (
            "Account Suspended\n\n"
            f"Hi {user_name or 'there'},\n\n"
            "Your Voice Ninja account has been suspended and you will not be able to access your account until it is reactivated.\n\n"
            + (f"Reason: {reason}\n\n" if reason else "")
            + "If you have any questions or believe this was a mistake, please contact our support team for more details.\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            plain_text_body=plain_text,
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

        content = (
            heading("Account Reactivated ✅")
            + paragraph(f"Hi {user_name or 'there'},")
            + callout(
                "<p style='margin:0;'>Good news — your Voice Ninja account is active again and you can resume using the service as normal.</p>",
                tone="success",
            )
            + signoff()
        )
        body = render_email(content, preheader="Your Voice Ninja account is active again")

        plain_text = (
            "Account Reactivated\n\n"
            f"Hi {user_name or 'there'},\n\n"
            "Good news — your Voice Ninja account is active again and you can resume using the service as normal.\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            plain_text_body=plain_text,
        )
    except Exception as e:
        logger.error(f"Failed to send account reactivated email: {str(e)}")


async def send_new_login_email(
    user_email: str,
    user_name: str | None,
    device_label: str | None,
    ip_address: str | None,
    occurred_at: datetime,
):
    """Sent whenever a new session is created for the user (fresh login on a device/browser)."""
    try:
        subject = "New Login to Your Voice Ninja Account"

        when_str = occurred_at.astimezone(timezone.utc).strftime("%b %d, %Y at %H:%M UTC")

        content = (
            heading("New Login Detected")
            + paragraph(f"Hi {user_name or 'there'},")
            + paragraph("We noticed a new login to your Voice Ninja account. Here are the details:")
            + info_table([
                ("Device", device_label or "Unknown device"),
                ("IP address", ip_address or "Unknown"),
                ("Time", when_str),
            ])
            + paragraph("If this was you, no action is needed. If you don't recognize this login, please review your active sessions from your account settings and log out any devices you don't recognize.")
            + signoff()
        )
        body = render_email(content, preheader="We noticed a new login to your account")

        plain_text = (
            "New Login Detected\n\n"
            f"Hi {user_name or 'there'},\n\n"
            "We noticed a new login to your Voice Ninja account. Here are the details:\n\n"
            f"Device: {device_label or 'Unknown device'}\n"
            f"IP address: {ip_address or 'Unknown'}\n"
            f"Time: {when_str}\n\n"
            "If this was you, no action is needed. If you don't recognize this login, please review your "
            "active sessions from your account settings and log out any devices you don't recognize.\n\n"
            "Thanks,\nVoice Ninja Team"
        )

        await send_email_async(
            subject=subject,
            recipients=[user_email],
            body=body,
            plain_text_body=plain_text,
        )
    except Exception as e:
        logger.error(f"Failed to send new login email: {str(e)}")


async def send_voice_limit_email_to_admins(db_session, user_identifier: str, user_id: int):
    """
    Sends an email to all admins notifying them about voice cloning limit reached.
    """
    subject = "Voice Cloning Limit Reached - Action Required"
    content = (
        heading("Voice Limit Reached Alert")
        + paragraph(
            f"User <b>{user_identifier}</b> (ID: {user_id}) attempted to create a custom cloned voice, "
            "but the ElevenLabs limits were reached or the current plan does not support instant voice cloning."
        )
        + paragraph("<b>Please update your ElevenLabs plan so users can continue cloning voices.</b>")
    )
    body = render_email(content, preheader="Voice cloning limit reached")
    plain_text = (
        "Voice Limit Reached Alert\n\n"
        f"User {user_identifier} (ID: {user_id}) attempted to create a custom cloned voice, but the "
        "ElevenLabs limits were reached or the current plan does not support instant voice cloning.\n\n"
        "Please update your ElevenLabs plan so users can continue cloning voices."
    )
    await send_email_to_admins(db_session, subject, body, plain_text_body=plain_text)
