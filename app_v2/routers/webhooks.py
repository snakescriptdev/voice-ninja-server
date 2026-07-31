"""
razorpay_webhook.py
────────────────────────────────────────────────────────────────────────────────
Razorpay webhook handler for one-time pay-as-you-go credit purchases.

Coin credit:
  Coins are credited in verify() (immediate, on payment confirmation, see
  coin_purchase.py) AND guarded here in payment.captured (in case the webhook
  arrives before verify() commits). The ledger idempotency check in both
  places prevents double-credit regardless of ordering.

Covered events:
  payment.captured, payment.failed, order.paid
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi_sqlalchemy import db
from sqlalchemy.exc import IntegrityError

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.databases.models import (
    AddOnCoinOrderModel,
    CoinsLedgerModel,
    PaymentModel,
    UnifiedAuthModel,
    WebhookEventLogModel,
)
from app_v2.schemas.enum_types import (
    CoinTransactionTypeEnum,
    PaymentProviderEnum,
    PaymentStatusEnum,
    PaymentTypeEnum,
)
from app_v2.utils.coin_utils import get_user_coin_balance
from app_v2.utils.email_service import send_payment_success_email, send_payment_failed_email
from app_v2.utils.invoice_utils import generate_invoice_pdf, generate_invoice_reference

logger = setup_logger(__name__)
router = APIRouter(prefix="/api/v2/webhooks", tags=["Webhooks"])

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

WEBHOOK_SECRET: str = VoiceSettings.RAZOR_WEBHOOK_SECRET

ORDER_EVENTS = {
    "payment.captured",
    "payment.failed",
    "order.paid",
}


# ──────────────────────────────────────────────────────────────────────────────
# Signature verification
# ──────────────────────────────────────────────────────────────────────────────

def _verify_webhook_signature(raw_body: bytes, rzp_signature: str) -> bool:
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, rzp_signature)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _log_event(
    event_id: str,
    event_type: str,
    payload: Dict[str, Any],
    status: str = "received",
) -> "WebhookEventLogModel":
    log = WebhookEventLogModel(
        provider="razorpay",
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        status=status,
    )
    db.session.add(log)
    db.session.flush()
    return log


def _mark_log(
    log: "WebhookEventLogModel",
    status: str,
    error: str | None = None,
) -> None:
    log.status = status
    log.error_message = error
    log.processed_at = datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────────────────
# Main webhook endpoint
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(request: Request):
    """
    Single entry-point for all Razorpay webhook events.

    Returns 200 in ALL cases (even on handler errors) so Razorpay does not
    retry. Business-logic failures are logged to WebhookEventLogModel.
    """
    raw_body: bytes = await request.body()

    # ── 1. Parse payload ──────────────────────────────────────────────────────
    try:
        payload: Dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error("Razorpay webhook: invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type: str = payload.get("event", "")
    event_id: str = (
        request.headers.get("X-Razorpay-Event-Id", "")
        or payload.get("id", "")
    )

    logger.info(f"Razorpay webhook received | event={event_type} | id={event_id}")

    if event_type not in ORDER_EVENTS:
        logger.info(f"Razorpay webhook: unhandled event type '{event_type}' – ignoring")
        return {"status": "ignored"}

    # ── 2. Idempotency guard ──────────────────────────────────────────────────
    if event_id:
        existing_log = (
            db.session.query(WebhookEventLogModel)
            .filter(
                WebhookEventLogModel.event_id == event_id,
                WebhookEventLogModel.status == "processed",
            )
            .first()
        )
        if existing_log:
            logger.info(f"Razorpay webhook: duplicate event {event_id} – skipping")
            return {"status": "duplicate"}

    # ── 3. Signature check ────────────────────────────────────────────────────
    # Required for every event, payment.failed included — an unsigned/forged
    # payment.failed can otherwise flip a real pending order to failed (the
    # order_id is visible client-side during checkout, so it's guessable).
    rzp_signature = request.headers.get("X-Razorpay-Signature", "")
    if not rzp_signature or not _verify_webhook_signature(raw_body, rzp_signature):
        logger.warning(
            f"Razorpay webhook: signature mismatch for {event_type} | id={event_id}"
        )
        try:
            with db():
                _log_event(event_id, event_type, payload, status="invalid_signature")
                db.session.commit()
        except Exception:
            logger.exception("Razorpay webhook: failed to log invalid-signature event")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # ── 4. Dispatch ───────────────────────────────────────────────────────────
    email_task: Optional[dict] = None
    try:
        with db():
            log = _log_event(event_id, event_type, payload)

            email_task = _handle_order_event(event_type, payload, log)

            _mark_log(log, "processed")
            db.session.commit()

    except Exception as exc:
        logger.exception(
            f"Razorpay webhook handler failed | event={event_type} | id={event_id} | error={exc}"
        )
        # Do NOT re-raise — return 200 so Razorpay doesn't retry infinitely.
        # Also don't send an email below for a transaction that may not have committed.
        email_task = None

    # Sent after the commit succeeds, outside the DB transaction — a slow SMTP
    # call has no business holding a DB session/lock open.
    if email_task:
        try:
            if email_task["type"] == "success":
                await send_payment_success_email(
                    user_email=email_task["email"],
                    user_name=email_task["name"],
                    amount=email_task["amount"],
                    currency=email_task["currency"],
                    provider_payment_id=email_task["provider_payment_id"],
                    base_url=VoiceSettings.FRONTEND_URL,
                    invoice_pdf=email_task.get("invoice_pdf"),
                )
            elif email_task["type"] == "failed":
                await send_payment_failed_email(
                    user_email=email_task["email"],
                    user_name=email_task["name"],
                    amount=email_task["amount"],
                    currency=email_task["currency"],
                    error_reason=email_task["error_reason"],
                    base_url=VoiceSettings.FRONTEND_URL,
                    invoice_pdf=email_task.get("invoice_pdf"),
                )
        except Exception:
            logger.exception("Razorpay webhook: failed to send payment email")

    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# Order / payment event handlers
# ──────────────────────────────────────────────────────────────────────────────

def _handle_order_event(
    event_type: str,
    payload: Dict[str, Any],
    log: "WebhookEventLogModel",
) -> Optional[dict]:
    payment_entity: Dict = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_entity: Dict = payload.get("payload", {}).get("order", {}).get("entity", {})

    if event_type == "payment.captured":
        return _order_payment_captured(payment_entity, order_entity, log)
    elif event_type == "payment.failed":
        return _order_payment_failed(payment_entity, order_entity, log)
    elif event_type == "order.paid":
        return _order_paid(payment_entity, order_entity, log)
    return None


def _order_payment_captured(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> Optional[dict]:
    """payment.captured is the source of truth for pay-as-you-go coin credits."""
    rzp_payment_id: str = payment_entity.get("id", "")
    rzp_order_id: str = payment_entity.get("order_id", "") or order_entity.get("id", "")

    if not rzp_order_id:
        logger.error("payment.captured: missing order_id")
        _mark_log(log, "failed", "missing order_id in payment entity")
        return

    existing = (
        db.session.query(PaymentModel)
        .filter(PaymentModel.provider_payment_id == rzp_payment_id)
        .first()
    )
    if existing:
        logger.info(f"payment.captured: payment {rzp_payment_id} already recorded – skipping")
        return

    addon_order: AddOnCoinOrderModel | None = (
        db.session.query(AddOnCoinOrderModel)
        .filter(AddOnCoinOrderModel.provider_order_id == rzp_order_id)
        .first()
    )

    if addon_order is None:
        logger.info(
            f"payment.captured: no addon_order for order {rzp_order_id} – skipping"
        )
        return

    if addon_order.status == PaymentStatusEnum.success:
        logger.info(
            f"payment.captured: addon_order {addon_order.id} already fulfilled – skipping"
        )
        return

    amount: float = float(payment_entity.get("amount", 0)) / 100.0
    currency: str = payment_entity.get("currency", "INR")

    # Savepoint: verify() (triggered by the browser) can be racing this exact
    # webhook delivery. Whichever insert commits first wins; the other hits the
    # unique index on provider_payment_id and backs off here instead of
    # double-crediting the ledger.
    try:
        with db.session.begin_nested():
            payment = PaymentModel(
                user_id=addon_order.user_id,
                amount=amount,
                currency=currency,
                status=PaymentStatusEnum.success,
                provider=PaymentProviderEnum.razorpay,
                provider_payment_id=rzp_payment_id or None,
                provider_order_id=rzp_order_id,
                payment_type=PaymentTypeEnum.coin_purchase,
                metadata_json={"coins": addon_order.coins, "source": "webhook"},
                invoice_reference=generate_invoice_reference(),
            )
            db.session.add(payment)
            db.session.flush()

            current_balance = get_user_coin_balance(addon_order.user_id)
            new_balance = current_balance + addon_order.coins

            ledger_entry = CoinsLedgerModel(
                user_id=addon_order.user_id,
                transaction_type=CoinTransactionTypeEnum.credit_purchase,
                coins=addon_order.coins,
                remaining_coins=addon_order.coins,
                reference_type="payment",
                reference_id=payment.id,
                balance_after=new_balance,
            )
            db.session.add(ledger_entry)

            addon_order.status = PaymentStatusEnum.success
            addon_order.provider_payment_id = rzp_payment_id
            addon_order.payment_id = payment.id
    except IntegrityError:
        logger.info(
            f"payment.captured: payment {rzp_payment_id} recorded concurrently "
            f"(verify() won the race) – skipping credit"
        )
        return

    logger.info(
        f"payment.captured (addon) | order={rzp_order_id} | "
        f"payment={rzp_payment_id} | coins={addon_order.coins} | user={addon_order.user_id}"
    )

    user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == addon_order.user_id).first()
    if not user or not user.email:
        return None
    return {
        "type": "success",
        "email": user.email,
        "name": user.name,
        "amount": amount,
        "currency": currency,
        "provider_payment_id": rzp_payment_id,
        "invoice_pdf": generate_invoice_pdf(payment, user),
    }


def _order_payment_failed(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> Optional[dict]:
    """payment.failed for a pay-as-you-go credit purchase order."""
    rzp_payment_id: str = payment_entity.get("id", "")
    rzp_order_id: str = payment_entity.get("order_id", "") or order_entity.get("id", "")

    if rzp_payment_id:
        already = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_payment_id == rzp_payment_id)
            .first()
        )
        if already:
            logger.info(f"payment.failed: {rzp_payment_id} already recorded – skipping")
            return

    if not rzp_order_id:
        logger.info("payment.failed: no order_id – skipping")
        return

    addon_order: AddOnCoinOrderModel | None = (
        db.session.query(AddOnCoinOrderModel)
        .filter(AddOnCoinOrderModel.provider_order_id == rzp_order_id)
        .first()
    )

    if addon_order is None:
        logger.info(
            f"payment.failed: order {rzp_order_id} not found in addon_coin_orders – skipping"
        )
        return

    if addon_order.status != PaymentStatusEnum.pending:
        logger.info(
            f"payment.failed: addon_order {addon_order.id} already in status "
            f"{addon_order.status} – skipping"
        )
        return

    try:
        with db.session.begin_nested():
            addon_order.status = PaymentStatusEnum.failed
            addon_order.provider_payment_id = rzp_payment_id

            failed_payment = PaymentModel(
                user_id=addon_order.user_id,
                amount=addon_order.amount,
                currency="INR",
                status=PaymentStatusEnum.failed,
                provider=PaymentProviderEnum.razorpay,
                provider_payment_id=rzp_payment_id or None,
                provider_order_id=rzp_order_id,
                payment_type=PaymentTypeEnum.coin_purchase,
                metadata_json={
                    "error_code": payment_entity.get("error_code"),
                    "error_description": payment_entity.get("error_description"),
                    "error_reason": payment_entity.get("error_reason"),
                    "source": "webhook",
                },
                invoice_reference=generate_invoice_reference(),
            )
            db.session.add(failed_payment)
    except IntegrityError:
        logger.info(
            f"payment.failed: payment {rzp_payment_id} recorded concurrently – skipping"
        )
        return

    logger.warning(
        f"payment.failed (addon) | order={rzp_order_id} | payment={rzp_payment_id} | "
        f"user={addon_order.user_id}"
    )

    user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == addon_order.user_id).first()
    if not user or not user.email:
        return None
    error_reason = (
        payment_entity.get("error_description")
        or payment_entity.get("error_reason")
        or "Payment was not completed"
    )
    return {
        "type": "failed",
        "email": user.email,
        "name": user.name,
        "amount": addon_order.amount,
        "currency": "INR",
        "error_reason": error_reason,
        "invoice_pdf": generate_invoice_pdf(failed_payment, user),
    }


def _order_paid(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> None:
    rzp_order_id: str = order_entity.get("id", "")
    logger.info(f"order.paid | order={rzp_order_id} – already handled by payment.captured")
