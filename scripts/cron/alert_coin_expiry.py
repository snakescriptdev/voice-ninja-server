import sys
import asyncio
from datetime import datetime, timezone
from elevenlabs import ElevenLabs

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

SMTP_USERNAME = ""
SMTP_PASSWORD = ""

FROM_EMAIL = SMTP_USERNAME
TO_EMAIL = []

FRONTEND_URL = ""
ELEVENLABS_API_KEY = ""

if not FRONTEND_URL or not ELEVENLABS_API_KEY or not TO_EMAIL or not SMTP_USERNAME or not SMTP_PASSWORD:
    print("Error: TO_EMAIL, SMTP_USERNAME, and SMTP_PASSWORD,FRONTEND_URL,ELEVENLABS_API_KEY  must be set in the environment variables.")
    sys.exit(1)

try:
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
except Exception as ex:
    print(f"Error initializing ElevenLabs client: {ex} with key : {ELEVENLABS_API_KEY}")
    sys.exit(1)

async def send_subscription_email(summary, credits_left):
    """
    Sends an HTML email containing ElevenLabs subscription details.
    """

    next_invoice = summary.get("next_invoice")

    amount_due = (
        f"${next_invoice['amount_due_usd']:.2f}"
        if next_invoice and next_invoice.get("amount_due_usd") is not None
        else "N/A"
    )

    next_payment = (
        next_invoice.get("next_payment_attempt")
        if next_invoice
        else "N/A"
    )

    overage = summary.get("current_overage", {})

    html = f"""
    <html>
    <body style="font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;padding:30px;">

    <table width="650" align="center" style="background:white;border-radius:10px;padding:25px;border-collapse:collapse;box-shadow:0 0 10px rgba(0,0,0,0.1);">

        <tr>
            <td>
                <h2 style="color:#1f2937;">
                    ElevenLabs Credit Alert
                </h2>

                <p>
                    Your ElevenLabs subscription summary is shown below.
                </p>

                <table width="100%" cellpadding="10" style="border-collapse:collapse;">

                    <tr style="background:#f8f9fa;">
                        <td><b>Subscription Tier</b></td>
                        <td>{summary.get("tier")}</td>
                    </tr>

                    <tr>
                        <td><b>Billing Period</b></td>
                        <td>{summary.get("billing_period")}</td>
                    </tr>

                    <tr style="background:#f8f9fa;">
                        <td><b>Characters Used</b></td>
                        <td>{summary.get("character_count"):,}</td>
                    </tr>

                    <tr>
                        <td><b>Character Limit</b></td>
                        <td>{summary.get("character_limit"):,}</td>
                    </tr>

                    <tr style="background:#f8f9fa;">
                        <td><b>Characters Remaining</b></td>
                        <td><b>{credits_left:,}</b></td>
                    </tr>

                    <tr>
                        <td><b>Current Overage</b></td>
                        <td>{overage.get("amount")} {overage.get("currency") or ""}</td>
                    </tr>

                    <tr style="background:#f8f9fa;">
                        <td><b>Open Invoices</b></td>
                        <td>{summary.get("has_open_invoices")}</td>
                    </tr>

                    <tr>
                        <td><b>Next Invoice Amount</b></td>
                        <td>{amount_due}</td>
                    </tr>

                    <tr style="background:#f8f9fa;">
                        <td><b>Next Payment Attempt</b></td>
                        <td>{next_payment}</td>
                    </tr>

                </table>

                <br>

                <div style="padding:15px;background:#fff3cd;border-radius:6px;border-left:5px solid #ffc107;">
                    <b>Warning</b><br>
                    Your account has <b>{credits_left:,}</b> characters remaining.
                    Consider purchasing additional credits if usage continues at the current rate.
                </div>

                <br>

                <p style="font-size:12px;color:gray;">
                    Generated automatically on {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}
                </p>

            </td>
        </tr>

    </table>

    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "⚠ ElevenLabs Credit Alert"
    msg["From"] = FROM_EMAIL
    msg["To"] = ", ".join(TO_EMAIL)

    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)

        server.sendmail(
            FROM_EMAIL,
            TO_EMAIL,          # List of recipients
            msg.as_string()    # Convert message to string
        )

    print("Email sent successfully.")

async def run_expiry_alert():
    """
    Cron job script to send warning emails when our Elevenlabs credits are about to expire.
    This script fetches the current subscription details and billing summary from ElevenLabs and checks for 
    any upcoming invoices or character count resets. If any issues arise during the process, it logs the error and 
    exits the script.
    """
    try:
        next_reset = None
        print(f"[{datetime.utcnow()}] Starting coin expiry alert process...")
        subscription = client.user.subscription.get()
        character_count = getattr(subscription, "character_count", 0)
        character_limit = getattr(subscription, "character_limit", 0)
        credits_left = character_limit - character_count

        billing_summary = {
                "tier": getattr(subscription, "tier", None),
                "currency": getattr(subscription, "currency", None),
                "billing_period": getattr(subscription, "billing_period", None),
                "has_open_invoices": getattr(subscription, "has_open_invoices", None),
                "character_count": getattr(subscription, "character_count", 0),
                "character_limit": getattr(subscription, "character_limit", 0),
                "next_character_count_reset": next_reset,
                "current_overage": {
                    "amount": getattr(getattr(subscription, "current_overage", None), "amount", None),
                    "currency": getattr(getattr(subscription, "current_overage", None), "currency", None)
                }
            }
        
        if getattr(subscription, "next_invoice", None):
            inv = subscription.next_invoice

            next_payment_attempt = None
            if getattr(inv, "next_payment_attempt_unix", None):
                try:
                    next_payment_attempt = datetime.fromtimestamp(
                        inv.next_payment_attempt_unix,
                        tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M:%S %Z")
                except Exception:
                    next_payment_attempt = None

            billing_summary["next_invoice"] = {
                "amount_due_usd": (
                    inv.amount_due_cents / 100
                    if getattr(inv, "amount_due_cents", None)
                    else None
                ),
                "next_payment_attempt": next_payment_attempt,
            }
        else:
            billing_summary["next_invoice"] = None

        if credits_left <= 10000:
            print(f"[{datetime.utcnow()}] Warning: Character count is low.")

            await send_subscription_email(
                billing_summary,
                credits_left
            )

        return {
            "status": "success",
            "credits used":character_count,
            "subscription_billing": billing_summary,
        }

    except Exception as e:
        print(f"Error during fetching current 11labs credits: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_expiry_alert())
