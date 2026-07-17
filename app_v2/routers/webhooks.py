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
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, status
from fastapi_sqlalchemy import db

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.databases.models import (
    AddOnCoinOrderModel,
    CoinsLedgerModel,
    PaymentModel,
    WebhookEventLogModel,
)
from app_v2.schemas.enum_types import (
    CoinTransactionTypeEnum,
    PaymentProviderEnum,
    PaymentStatusEnum,
    PaymentTypeEnum,
)
from app_v2.utils.coin_utils import get_user_coin_balance

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
    rzp_signature = request.headers.get("X-Razorpay-Signature", "")
    signature_valid = False

    if rzp_signature and _verify_webhook_signature(raw_body, rzp_signature):
        signature_valid = True
    else:
        if event_type == "payment.failed":
            logger.warning(
                f"Razorpay webhook: processing payment.failed with missing/invalid signature | id={event_id}"
            )
        else:
            logger.warning(
                f"Razorpay webhook: signature mismatch for {event_type} | id={event_id}"
            )
            raise HTTPException(status_code=400, detail="Invalid signature")

    # ── 4. Dispatch ───────────────────────────────────────────────────────────
    try:
        with db():
            log = _log_event(event_id, event_type, payload)
            if not signature_valid:
                log.status = "invalid_signature"

            _handle_order_event(event_type, payload, log)

            _mark_log(log, "processed")
            db.session.commit()

    except Exception as exc:
        logger.exception(
            f"Razorpay webhook handler failed | event={event_type} | id={event_id} | error={exc}"
        )
        # Do NOT re-raise — return 200 so Razorpay doesn't retry infinitely.

    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# Order / payment event handlers
# ──────────────────────────────────────────────────────────────────────────────

def _handle_order_event(
    event_type: str,
    payload: Dict[str, Any],
    log: "WebhookEventLogModel",
) -> None:
    payment_entity: Dict = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_entity: Dict = payload.get("payload", {}).get("order", {}).get("entity", {})

    if event_type == "payment.captured":
        _order_payment_captured(payment_entity, order_entity, log)
    elif event_type == "payment.failed":
        _order_payment_failed(payment_entity, order_entity, log)
    elif event_type == "order.paid":
        _order_paid(payment_entity, order_entity, log)


def _order_payment_captured(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> None:
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

    payment = PaymentModel(
        user_id=addon_order.user_id,
        amount=amount,
        currency=currency,
        status=PaymentStatusEnum.success,
        provider=PaymentProviderEnum.razorpay,
        provider_payment_id=rzp_payment_id,
        provider_order_id=rzp_order_id,
        payment_type=PaymentTypeEnum.coin_purchase,
        metadata_json={"coins": addon_order.coins, "source": "webhook"},
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

    logger.info(
        f"payment.captured (addon) | order={rzp_order_id} | "
        f"payment={rzp_payment_id} | coins={addon_order.coins} | user={addon_order.user_id}"
    )


def _order_payment_failed(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> None:
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

    addon_order.status = PaymentStatusEnum.failed
    addon_order.provider_payment_id = rzp_payment_id

    failed_payment = PaymentModel(
        user_id=addon_order.user_id,
        amount=addon_order.amount,
        currency="INR",
        status=PaymentStatusEnum.failed,
        provider=PaymentProviderEnum.razorpay,
        provider_payment_id=rzp_payment_id,
        provider_order_id=rzp_order_id,
        payment_type=PaymentTypeEnum.coin_purchase,
        metadata_json={
            "error_code": payment_entity.get("error_code"),
            "error_description": payment_entity.get("error_description"),
            "error_reason": payment_entity.get("error_reason"),
            "source": "webhook",
        },
    )
    db.session.add(failed_payment)
    logger.warning(
        f"payment.failed (addon) | order={rzp_order_id} | payment={rzp_payment_id} | "
        f"user={addon_order.user_id}"
    )


def _order_paid(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> None:
    rzp_order_id: str = order_entity.get("id", "")
    logger.info(f"order.paid | order={rzp_order_id} – already handled by payment.captured")
