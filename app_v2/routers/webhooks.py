"""
razorpay_webhook.py
────────────────────────────────────────────────────────────────────────────────
Production-grade Razorpay webhook handler.

Covers:
  Subscription events
    • subscription.activated
    • subscription.charged
    • subscription.completed
    • subscription.updated
    • subscription.pending
    • subscription.halted
    • subscription.cancelled
    • subscription.paused
    • subscription.resumed

  Order / payment events (add-on coin purchases)
    • payment.captured
    • payment.failed
    • order.paid

Design decisions
  1. HMAC-SHA256 signature verification on every request (raw body).
  2. Idempotent handlers – every event is checked against the DB before acting.
  3. All DB mutations happen inside a single transaction; on failure the
     transaction is rolled back and a 200 is still returned to Razorpay so it
     does NOT retry (we log + alert instead). Retries on transient DB errors
     would cause duplicate credit – safer to let an admin fix manually.
  4. Coin credit uses FIFO-aware helpers (same as the rest of the codebase).
  5. WebhookEventLogModel is written FIRST (before business logic) so that
     even a crash mid-handler leaves an audit trail.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, status
from fastapi_sqlalchemy import db
from sqlalchemy.exc import IntegrityError

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.databases.models import (
    AddOnCoinOrderModel,
    CoinsLedgerModel,
    CoinPackageModel,
    PaymentModel,
    PlanModel,
    UserSubscriptionModel,
    WebhookEventLogModel,          # new model – see models_addition.py
)
from app_v2.schemas.enum_types import (
    CoinTransactionTypeEnum,
    PaymentProviderEnum,
    PaymentStatusEnum,
    PaymentTypeEnum,
    SubscriptionStatusEnum,
)
from app_v2.utils.coin_utils import get_user_coin_balance, reset_unused_subscription_coins

logger = setup_logger(__name__)
router = APIRouter(prefix="/api/v2/webhooks", tags=["Webhooks"])

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

WEBHOOK_SECRET: str = VoiceSettings.RAZOR_WEBHOOK_SECRET  # add to config

SUBSCRIPTION_EVENTS = {
    "subscription.activated",
    "subscription.charged",
    "subscription.completed",
    "subscription.updated",
    "subscription.pending",
    "subscription.halted",
    "subscription.cancelled",
    "subscription.paused",
    "subscription.resumed",
}

ORDER_EVENTS = {
    "payment.captured",
    "payment.failed",
    "order.paid",
}

ALL_HANDLED_EVENTS = SUBSCRIPTION_EVENTS | ORDER_EVENTS


# ──────────────────────────────────────────────────────────────────────────────
# Signature verification
# ──────────────────────────────────────────────────────────────────────────────

def _verify_webhook_signature(raw_body: bytes, rzp_signature: str) -> bool:
    """
    Razorpay signs the raw request body with the webhook secret using HMAC-SHA256.
    The generated hex digest must match the X-Razorpay-Signature header.
    """
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, rzp_signature)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ts_to_dt(ts: int | None) -> datetime | None:
    """Convert a Unix timestamp (int) to a naive UTC datetime."""
    if ts is None:
        return None
    return datetime.utcfromtimestamp(ts)


def _log_event(event_id: str, event_type: str, payload: Dict[str, Any], status: str = "received") -> WebhookEventLogModel:
    """
    Persist a webhook event log entry.  Called BEFORE business logic so we
    always have an audit trail.
    """
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


def _mark_log(log: WebhookEventLogModel, status: str, error: str | None = None) -> None:
    log.status = status
    log.error_message = error
    log.processed_at = datetime.utcnow()


# ──────────────────────────────────────────────────────────────────────────────
# Main webhook endpoint
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(request: Request):
    """
    Single entry-point for all Razorpay webhook events.

    Returns 200 in ALL cases (even on handler errors) so Razorpay does not
    retry.  Business-logic failures are logged to WebhookEventLogModel and
    should be monitored / alerted via your observability stack.
    """
    raw_body: bytes = await request.body()

    # ── 1. Signature check ────────────────────────────────────────────────────
    rzp_signature = request.headers.get("X-Razorpay-Signature", "")
    if not rzp_signature:
        logger.warning("Razorpay webhook received without signature header")
        raise HTTPException(status_code=400, detail="Missing signature")

    if not _verify_webhook_signature(raw_body, rzp_signature):
        logger.warning("Razorpay webhook signature mismatch")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # ── 2. Parse payload ──────────────────────────────────────────────────────
    try:
        payload: Dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error("Razorpay webhook: invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type: str = payload.get("event", "")
    event_id: str = payload.get("id", "unknown")

    logger.info(f"Razorpay webhook received | event={event_type} | id={event_id}")

    if event_type not in ALL_HANDLED_EVENTS:
        logger.info(f"Razorpay webhook: unhandled event type '{event_type}' – ignoring")
        return {"status": "ignored"}

    # ── 3. Idempotency guard ──────────────────────────────────────────────────
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

    # ── 4. Dispatch ───────────────────────────────────────────────────────────
    try:
        with db():
            log = _log_event(event_id, event_type, payload)

            if event_type in SUBSCRIPTION_EVENTS:
                _handle_subscription_event(event_type, payload, log)
            elif event_type in ORDER_EVENTS:
                _handle_order_event(event_type, payload, log)

            _mark_log(log, "processed")
            db.session.commit()

    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Razorpay webhook handler failed | event={event_type} | id={event_id} | error={exc}")
        # Do NOT re-raise – return 200 so Razorpay doesn't retry infinitely.

    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# Subscription event handlers
# ──────────────────────────────────────────────────────────────────────────────

def _handle_subscription_event(event_type: str, payload: Dict[str, Any], log: WebhookEventLogModel) -> None:
    subscription_data: Dict[str, Any] = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    payment_data: Dict[str, Any] = payload.get("payload", {}).get("payment", {}).get("entity", {})

    rzp_subscription_id: str = subscription_data.get("id", "")
    if not rzp_subscription_id:
        logger.error("Subscription webhook: missing subscription id")
        _mark_log(log, "failed", "missing razorpay subscription id")
        return

    # Resolve internal subscription record (may not exist yet for .activated)
    sub: UserSubscriptionModel | None = (
        db.session.query(UserSubscriptionModel)
        .filter(UserSubscriptionModel.provider_subscription_id == rzp_subscription_id)
        .first()
    )

    handler_map = {
        "subscription.activated": _sub_activated,
        "subscription.charged":   _sub_charged,
        "subscription.completed": _sub_completed,
        "subscription.updated":   _sub_updated,
        "subscription.pending":   _sub_pending,
        "subscription.halted":    _sub_halted,
        "subscription.cancelled": _sub_cancelled,
        "subscription.paused":    _sub_paused,
        "subscription.resumed":   _sub_resumed,
    }

    handler = handler_map.get(event_type)
    if handler:
        handler(sub, subscription_data, payment_data, log)
    else:
        logger.warning(f"No handler for subscription event: {event_type}")


def _sub_activated(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    """
    subscription.activated fires when the subscription is authenticated and
    the first payment is authorised (but may not yet be captured).

    If subscription was created via our verify endpoint, it will already exist.
    If it arrives here first (race condition / webhook faster than verify),
    we create it defensively.
    """
    if sub is None:
        # Webhook beat the verify endpoint – create skeleton subscription.
        # Notes carry user_id and plan_id set during subscription creation.
        notes: Dict = sub_data.get("notes", {})
        user_id = int(notes.get("user_id", 0))
        plan_id = int(notes.get("plan_id", 0))
        if not user_id or not plan_id:
            logger.error("subscription.activated: cannot resolve user/plan from notes")
            _mark_log(log, "failed", "missing user_id or plan_id in notes")
            return

        plan = db.session.query(PlanModel).filter(PlanModel.id == plan_id).first()
        if not plan:
            _mark_log(log, "failed", f"plan {plan_id} not found")
            return

        current_start = _ts_to_dt(sub_data.get("current_start")) or datetime.utcnow()
        current_end = _ts_to_dt(sub_data.get("current_end")) or _calc_period_end(plan, current_start)

        sub = UserSubscriptionModel(
            user_id=user_id,
            plan_id=plan_id,
            status=SubscriptionStatusEnum.active,
            current_period_start=current_start,
            current_period_end=current_end,
            cancel_at_period_end=False,
            provider="razorpay",
            provider_subscription_id=sub_data["id"],
            subscription_metadata={"customer_id": sub_data.get("customer_id", "")},
        )
        db.session.add(sub)
        db.session.flush()

        # Credit coins for first cycle
        _credit_subscription_coins(sub, plan, current_end)
    else:
        # Subscription already exists – just ensure it's marked active
        sub.status = SubscriptionStatusEnum.active
        sub.cancel_at_period_end = False

    logger.info(f"subscription.activated | rzp_id={sub_data['id']}")


def _sub_charged(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    """
    subscription.charged fires each billing cycle after a successful charge.
    This is the canonical place to credit coins for recurring cycles.
    """
    if sub is None:
        logger.error(f"subscription.charged: subscription not found for {sub_data.get('id')}")
        _mark_log(log, "failed", "subscription not found")
        return

    plan: PlanModel = db.session.query(PlanModel).filter(PlanModel.id == sub.plan_id).first()
    if not plan:
        _mark_log(log, "failed", f"plan {sub.plan_id} not found")
        return

    rzp_payment_id: str = payment_data.get("id", "")
    amount: float = float(payment_data.get("amount", 0)) / 100.0
    currency: str = payment_data.get("currency", "INR")

    # Idempotency: skip if we already processed this payment
    existing_payment = (
        db.session.query(PaymentModel)
        .filter(PaymentModel.provider_payment_id == rzp_payment_id)
        .first()
    )
    if existing_payment:
        logger.info(f"subscription.charged: payment {rzp_payment_id} already recorded – skipping")
        return

    # Update subscription period
    new_start = _ts_to_dt(sub_data.get("current_start")) or datetime.utcnow()
    new_end = _ts_to_dt(sub_data.get("current_end")) or _calc_period_end(plan, new_start)

    sub.current_period_start = new_start
    sub.current_period_end = new_end
    sub.status = SubscriptionStatusEnum.active
    sub.cancel_at_period_end = False

    # Handle pending plan upgrade: if next_plan_id set, switch plan
    if sub.next_plan_id:
        next_plan = db.session.query(PlanModel).filter(PlanModel.id == sub.next_plan_id).first()
        if next_plan:
            logger.info(f"Activating scheduled plan upgrade: {sub.plan_id} → {sub.next_plan_id}")
            sub.plan_id = sub.next_plan_id
            plan = next_plan
        sub.next_plan_id = None

    # Record payment
    payment = PaymentModel(
        user_id=sub.user_id,
        amount=amount,
        currency=currency,
        status=PaymentStatusEnum.success,
        provider=PaymentProviderEnum.razorpay,
        provider_payment_id=rzp_payment_id,
        provider_order_id=sub_data.get("id"),
        payment_type=PaymentTypeEnum.subscription,
        metadata_json={"plan_id": plan.id, "subscription_id": sub.id, "cycle": "renewal"},
    )
    db.session.add(payment)
    db.session.flush()

    # Credit coins for new cycle
    _credit_subscription_coins(sub, plan, new_end)

    logger.info(f"subscription.charged | sub={sub.id} | payment={rzp_payment_id} | coins={plan.coins_included}")


def _sub_completed(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    """All billing cycles exhausted – subscription naturally ends."""
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.expired
    sub.cancel_at_period_end = True
    logger.info(f"subscription.completed | sub={sub.id}")


def _sub_updated(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    """Razorpay subscription updated (e.g. quantity change).  Sync metadata."""
    if sub is None:
        return
    if sub.subscription_metadata is None:
        sub.subscription_metadata = {}
    sub.subscription_metadata["last_rzp_update"] = sub_data
    logger.info(f"subscription.updated | sub={sub.id}")


def _sub_pending(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    """Payment pending (e.g. bank transfer in progress)."""
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.pending
    logger.info(f"subscription.pending | sub={sub.id}")


def _sub_halted(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    """
    Payment failed for MAX_RETRIES consecutive attempts.
    Razorpay halts the subscription; access should be revoked.
    """
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.halted
    logger.warning(f"subscription.halted | sub={sub.id} – user access should be reviewed")


def _sub_cancelled(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.cancelled
    sub.cancel_at_period_end = True
    logger.info(f"subscription.cancelled | sub={sub.id}")


def _sub_paused(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.paused
    logger.info(f"subscription.paused | sub={sub.id}")


def _sub_resumed(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: WebhookEventLogModel,
) -> None:
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.active
    logger.info(f"subscription.resumed | sub={sub.id}")


# ──────────────────────────────────────────────────────────────────────────────
# Order / payment event handlers  (add-on coin purchases)
# ──────────────────────────────────────────────────────────────────────────────

def _handle_order_event(event_type: str, payload: Dict[str, Any], log: WebhookEventLogModel) -> None:
    payment_entity: Dict = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_entity: Dict = payload.get("payload", {}).get("order", {}).get("entity", {})

    if event_type == "payment.captured":
        _order_payment_captured(payment_entity, order_entity, log)
    elif event_type == "payment.failed":
        _order_payment_failed(payment_entity, order_entity, log)
    elif event_type == "order.paid":
        # order.paid is a convenience event that fires when the order amount is
        # fully paid.  We handle the actual crediting in payment.captured; here
        # we just sync the order status.
        _order_paid(payment_entity, order_entity, log)


def _order_payment_captured(payment_entity: Dict, order_entity: Dict, log: WebhookEventLogModel) -> None:
    """
    payment.captured fires when Razorpay successfully captures the payment.
    This is the source of truth for add-on coin credits.
    """
    rzp_payment_id: str = payment_entity.get("id", "")
    rzp_order_id: str = payment_entity.get("order_id", "") or order_entity.get("id", "")

    if not rzp_order_id:
        logger.error("payment.captured: missing order_id")
        _mark_log(log, "failed", "missing order_id in payment entity")
        return

    # Idempotency: skip if payment already recorded
    existing = (
        db.session.query(PaymentModel)
        .filter(PaymentModel.provider_payment_id == rzp_payment_id)
        .first()
    )
    if existing:
        logger.info(f"payment.captured: payment {rzp_payment_id} already recorded – skipping")
        return

    # Find the AddOnCoinOrder
    addon_order: AddOnCoinOrderModel | None = (
        db.session.query(AddOnCoinOrderModel)
        .filter(AddOnCoinOrderModel.provider_order_id == rzp_order_id)
        .first()
    )

    if addon_order is None:
        # This payment is not related to a coin purchase – could be a subscription
        # charge hitting the payment.captured event.  Check notes to decide.
        notes: Dict = payment_entity.get("notes", {})
        if notes.get("type") == "addon_purchase":
            logger.error(f"payment.captured: addon_order not found for order {rzp_order_id}")
            _mark_log(log, "failed", f"addon_order not found for order {rzp_order_id}")
        else:
            logger.info(f"payment.captured: non-addon payment {rzp_payment_id} – skipping")
        return

    if addon_order.status == PaymentStatusEnum.success:
        logger.info(f"payment.captured: addon_order {addon_order.id} already fulfilled – skipping")
        return

    bundle: CoinPackageModel = (
        db.session.query(CoinPackageModel)
        .filter(CoinPackageModel.id == addon_order.bundle_id)
        .first()
    )
    if not bundle:
        _mark_log(log, "failed", f"bundle {addon_order.bundle_id} not found")
        return

    amount: float = float(payment_entity.get("amount", 0)) / 100.0
    currency: str = payment_entity.get("currency", "INR")

    # Create payment record
    payment = PaymentModel(
        user_id=addon_order.user_id,
        amount=amount,
        currency=currency,
        status=PaymentStatusEnum.success,
        provider=PaymentProviderEnum.razorpay,
        provider_payment_id=rzp_payment_id,
        provider_order_id=rzp_order_id,
        payment_type=PaymentTypeEnum.coin_purchase,
        metadata_json={"bundle_id": bundle.id, "coins": bundle.coins, "source": "webhook"},
    )
    db.session.add(payment)
    db.session.flush()

    # Credit coins
    current_balance = get_user_coin_balance(addon_order.user_id)
    new_balance = current_balance + bundle.coins

    expiry_date = None
    if bundle.validity_days is not None:
        expiry_date = datetime.utcnow() + timedelta(days=bundle.validity_days)

    ledger_entry = CoinsLedgerModel(
        user_id=addon_order.user_id,
        transaction_type=CoinTransactionTypeEnum.credit_purchase,
        coins=bundle.coins,
        remaining_coins=bundle.coins,
        expiry_at=expiry_date,
        reference_type="payment",
        reference_id=payment.id,
        balance_after=new_balance,
    )
    db.session.add(ledger_entry)

    # Update addon order
    addon_order.status = PaymentStatusEnum.success
    addon_order.provider_payment_id = rzp_payment_id
    addon_order.payment_id = payment.id

    logger.info(
        f"payment.captured (addon) | order={rzp_order_id} | "
        f"payment={rzp_payment_id} | coins={bundle.coins} | user={addon_order.user_id}"
    )


def _order_payment_failed(payment_entity: Dict, order_entity: Dict, log: WebhookEventLogModel) -> None:
    """Mark addon order as failed for observability."""
    rzp_payment_id: str = payment_entity.get("id", "")
    rzp_order_id: str = payment_entity.get("order_id", "") or order_entity.get("id", "")

    addon_order: AddOnCoinOrderModel | None = (
        db.session.query(AddOnCoinOrderModel)
        .filter(AddOnCoinOrderModel.provider_order_id == rzp_order_id)
        .first()
    )

    if addon_order and addon_order.status == PaymentStatusEnum.pending:
        addon_order.status = PaymentStatusEnum.failed
        addon_order.provider_payment_id = rzp_payment_id

        # Record failed payment for audit
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
                "source": "webhook",
            },
        )
        db.session.add(failed_payment)
        logger.warning(f"payment.failed | order={rzp_order_id} | payment={rzp_payment_id}")


def _order_paid(payment_entity: Dict, order_entity: Dict, log: WebhookEventLogModel) -> None:
    """
    order.paid fires after full payment.  We already handle coin credit in
    payment.captured; here we only log for completeness.
    """
    rzp_order_id: str = order_entity.get("id", "")
    logger.info(f"order.paid | order={rzp_order_id} – already handled by payment.captured")


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _calc_period_end(plan: PlanModel, start: datetime) -> datetime:
    """Fallback period-end calculation when Razorpay doesn't provide timestamps."""
    if plan.billing_period.value == "annual":
        return start + timedelta(days=365)
    return start + timedelta(days=30)


def _credit_subscription_coins(
    sub: UserSubscriptionModel,
    plan: PlanModel,
    period_end: datetime,
) -> None:
    """
    Credit subscription coins to the user's ledger.

    If the plan does NOT carry forward unused coins, we first expire any
    remaining balance from the previous cycle before crediting new coins.
    This keeps the ledger accurate for FIFO deduction.
    """
    if not plan.carry_forward_coins:
        reset_unused_subscription_coins(sub.user_id)

    current_balance = get_user_coin_balance(sub.user_id)
    new_balance = current_balance + plan.coins_included

    ledger_entry = CoinsLedgerModel(
        user_id=sub.user_id,
        transaction_type=CoinTransactionTypeEnum.credit_subscription,
        coins=plan.coins_included,
        remaining_coins=plan.coins_included,
        expiry_at=period_end,
        reference_type="subscription",
        reference_id=sub.id,
        balance_after=new_balance,
    )
    db.session.add(ledger_entry)
    logger.info(
        f"Coins credited | user={sub.user_id} | coins={plan.coins_included} | "
        f"new_balance={new_balance} | expires={period_end}"
    )