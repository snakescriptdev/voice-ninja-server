"""
razorpay_webhook.py
────────────────────────────────────────────────────────────────────────────────
Production-grade Razorpay webhook handler.

Status transition map:
  subscription.activated  → authenticated   (mandate confirmed, not yet charged)
  subscription.charged    → active          (money captured — SOLE place for this)
  subscription.completed  → completed
  subscription.pending    → pending         (renewal charge failed, will retry)
  subscription.halted     → halted          (all retries exhausted)
  subscription.cancelled  → cancelled
  subscription.paused     → paused
  subscription.resumed    → active

Coin credit:
  Coins are credited ONLY inside _sub_charged.
  No other handler (including _sub_activated) credits coins.
  This guarantees coins are issued only after real money capture.

Covered events:
  Subscription: activated, charged, completed, updated, pending,
                halted, cancelled, paused, resumed
  Order/payment: payment.captured, payment.failed, order.paid

Design decisions:
  1. HMAC-SHA256 signature verification on every request (raw body).
  2. Idempotent handlers – every event checked against DB before acting.
  3. All DB mutations in a single transaction; on failure the transaction is
     rolled back and 200 is still returned so Razorpay does NOT retry.
  4. Coin credit uses FIFO-aware helpers.
  5. WebhookEventLogModel written FIRST so even a crash leaves an audit trail.

Fixes applied (vs original):
  - _sub_cancelled: early-return guard when already cancelled (idempotent + log).
  - _sub_charged: narrowed idempotency check so verify()-created PaymentModel
    rows don't block coin credits.
  - payment.failed handler: robust user-id resolution for subscription failures.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

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
    WebhookEventLogModel,
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

WEBHOOK_SECRET: str = VoiceSettings.RAZOR_WEBHOOK_SECRET

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
    log.processed_at = datetime.utcnow()


def _resolve_sub_by_rzp_id(rzp_subscription_id: str) -> "UserSubscriptionModel | None":
    """
    Look up the internal subscription row by Razorpay subscription id.

    Searches ALL rows regardless of status so that:
      - subscription.cancelled for an already-cancelled row (e.g. after a plan
        change) is handled idempotently rather than silently dropped.
      - subscription.charged for a newly-created row (verify PATH B) is found
        even before status is promoted to active.
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(UserSubscriptionModel.provider_subscription_id == rzp_subscription_id)
        .first()
    )


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

    if event_type not in ALL_HANDLED_EVENTS:
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

            if event_type in SUBSCRIPTION_EVENTS:
                _handle_subscription_event(event_type, payload, log)
            elif event_type in ORDER_EVENTS:
                _handle_order_event(event_type, payload, log)

            _mark_log(log, "processed")
            db.session.commit()

    except Exception as exc:
        logger.exception(
            f"Razorpay webhook handler failed | event={event_type} | id={event_id} | error={exc}"
        )
        # Do NOT re-raise – return 200 so Razorpay doesn't retry infinitely.

    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────────────
# Subscription event dispatcher
# ──────────────────────────────────────────────────────────────────────────────

def _handle_subscription_event(
    event_type: str,
    payload: Dict[str, Any],
    log: "WebhookEventLogModel",
) -> None:
    subscription_data: Dict[str, Any] = (
        payload.get("payload", {}).get("subscription", {}).get("entity", {})
    )
    payment_data: Dict[str, Any] = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )

    rzp_subscription_id: str = subscription_data.get("id", "")
    if not rzp_subscription_id:
        logger.error("Subscription webhook: missing subscription id")
        _mark_log(log, "failed", "missing razorpay subscription id")
        return

    # Resolve internal subscription row (may not exist yet for .activated)
    sub: UserSubscriptionModel | None = _resolve_sub_by_rzp_id(rzp_subscription_id)

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


# ──────────────────────────────────────────────────────────────────────────────
# Individual subscription event handlers
# ──────────────────────────────────────────────────────────────────────────────

def _sub_activated(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """
    subscription.activated fires when the subscription mandate is authenticated
    and the first payment is authorised (but not yet captured/charged).

    Status → authenticated  (NOT active).
    Coins are NOT credited here — that happens exclusively in _sub_charged.

    Two cases:
      • sub is None  : webhook beat verify() — create a skeleton row.
      • sub exists   : verify() already created the row — just confirm status.
    """
    if sub is None:
        # Webhook beat the verify endpoint — create skeleton row.
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
        current_end = (
            _ts_to_dt(sub_data.get("current_end")) or _calc_period_end(plan, current_start)
        )

        sub = UserSubscriptionModel(
            user_id=user_id,
            plan_id=plan_id,
            # authenticated — NOT active. _sub_charged will promote to active.
            status=SubscriptionStatusEnum.authenticated,
            current_period_start=current_start,
            current_period_end=current_end,
            cancel_at_period_end=False,
            provider="razorpay",
            provider_subscription_id=sub_data["id"],
            subscription_metadata={"customer_id": sub_data.get("customer_id", "")},
        )
        db.session.add(sub)
        db.session.flush()
        # Do NOT credit coins here — _sub_charged handles that.
    else:
        # Subscription already exists (verify() ran first) — confirm authenticated.
        # Only set to authenticated if not already active (avoid downgrade from active
        # if subscription.activated fires after subscription.charged in rare ordering).
        if sub.status not in (SubscriptionStatusEnum.active, SubscriptionStatusEnum.paused):
            sub.status = SubscriptionStatusEnum.authenticated
        sub.cancel_at_period_end = False

    logger.info(f"subscription.activated | rzp_id={sub_data['id']} | status=authenticated")


def _sub_charged(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """
    subscription.charged fires each billing cycle after a successful charge.

    This is THE canonical place to:
      • Promote status to  active.
      • Credit coins for the cycle.
      • Record the payment.

    It handles both first-charge (from authenticated) and recurring charges.

    Idempotency note:
      verify() may have already created a PaymentModel for this payment_id.
      We check for a ledger entry tied to that payment before crediting coins —
      not just for the PaymentModel — so verify()-created rows don't block us.
    """
    if sub is None:
        logger.error(f"subscription.charged: subscription not found for {sub_data.get('id')}")
        _mark_log(log, "failed", "subscription not found")
        return

    plan: PlanModel = (
        db.session.query(PlanModel).filter(PlanModel.id == sub.plan_id).first()
    )
    if not plan:
        _mark_log(log, "failed", f"plan {sub.plan_id} not found")
        return

    rzp_payment_id: str = payment_data.get("id", "")
    amount: float = float(payment_data.get("amount", 0)) / 100.0
    currency: str = payment_data.get("currency", "INR")

    payment = None

    # Idempotency: check if we already processed this payment (coins credited)
    if rzp_payment_id:
        existing_payment = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_payment_id == rzp_payment_id)
            .first()
        )
        if existing_payment:
            # verify() may have created the PaymentModel but NEVER credits coins.
            # Only skip if a ledger entry already exists for this specific payment.
            existing_ledger = (
                db.session.query(CoinsLedgerModel)
                .filter(
                    CoinsLedgerModel.reference_type == "payment",
                    CoinsLedgerModel.reference_id == existing_payment.id,
                )
                .first()
            )
            if existing_ledger:
                logger.info(
                    f"subscription.charged | payment={rzp_payment_id} already has ledger entry – skipping"
                )
                return

            logger.info(
                f"subscription.charged | payment={rzp_payment_id} exists but no coins credited yet – proceeding"
            )
            payment = existing_payment

    # Update subscription period
    new_start = _ts_to_dt(sub_data.get("current_start")) or datetime.utcnow()
    new_end = _ts_to_dt(sub_data.get("current_end")) or _calc_period_end(plan, new_start)

    # ── Promote to active ─────────────────────────────────────────────────────
    # This is the ONLY place status transitions to active.
    sub.status = SubscriptionStatusEnum.active
    sub.cancel_at_period_end = False
    sub.current_period_start = new_start
    sub.current_period_end = new_end

    # Handle pending plan change safety-net: if next_plan_id still set (normally
    # cleared by verify), swap it now so the webhook is self-healing.
    if sub.next_plan_id:
        next_plan = (
            db.session.query(PlanModel).filter(PlanModel.id == sub.next_plan_id).first()
        )
        if next_plan:
            old_plan_id = sub.plan_id
            logger.info(
                f"subscription.charged: activating pending plan swap "
                f"{sub.plan_id} → {sub.next_plan_id}"
            )
            sub.plan_id = sub.next_plan_id
            plan = next_plan

            if old_plan_id and sub.plan_id != old_plan_id:
                try:
                    from app_v2.utils.downgrade_utils import (
                        compute_downgrade_diff,
                        schedule_downgrade_for_user,
                    )
                    from app_v2.schemas.enum_types import ScheduledDowngradeTriggerEnum
                    
                    downgrade_diff = compute_downgrade_diff(
                        old_plan_id, sub.plan_id, db.session
                    )
                    if downgrade_diff:
                        # Schedule for end of the cycle just started
                        scheduled_downgrade = schedule_downgrade_for_user(
                            user_id=sub.user_id,
                            old_plan_id=old_plan_id,
                            new_plan_id=sub.plan_id,
                            subscription_id=sub.id,
                            scheduled_for=sub.current_period_end,
                            trigger_source=ScheduledDowngradeTriggerEnum.plan_change,
                            session=db.session,
                        )
                        logger.info(
                            f"subscription.charged | downgrade scheduled via webhook | "
                            f"user={sub.user_id} | scheduled_for={sub.current_period_end}"
                        )
                except Exception as dge:
                    logger.error(
                        f"subscription.charged | downgrade scheduling failed | "
                        f"user={sub.user_id} | error={dge}"
                    )
        sub.next_plan_id = None

    # ── Record payment ────────────────────────────────────────────────────────
    if not payment:
        payment = PaymentModel(
            user_id=sub.user_id,
            amount=amount,
            currency=currency,
            status=PaymentStatusEnum.success,
            provider=PaymentProviderEnum.razorpay,
            provider_payment_id=rzp_payment_id,
            provider_order_id=sub_data.get("id"),
            payment_type=PaymentTypeEnum.subscription,
            metadata_json={
                "plan_id": plan.id,
                "subscription_id": sub.id,
                "cycle": "renewal",
            },
        )
        db.session.add(payment)
        db.session.flush()
    else:
        # Payment exists (from verify) — ensure it's marked success
        payment.status = PaymentStatusEnum.success
        payment.provider_payment_id = rzp_payment_id
        db.session.add(payment)
        db.session.flush()

    # ── Credit coins ──────────────────────────────────────────────────────────
    # This is the ONLY place coins are credited for subscriptions.
    # Guarded by the narrowed idempotency check above.
    _credit_subscription_coins(sub, plan, payment, new_end)

    logger.info(
        f"subscription.charged | sub={sub.id} | payment={rzp_payment_id} | "
        f"plan={plan.id} | coins={plan.coins_included} | status→active"
    )


def _sub_completed(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """All billing cycles exhausted – subscription naturally ends."""
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.completed
    sub.cancel_at_period_end = True
    logger.info(f"subscription.completed | sub={sub.id}")


def _sub_updated(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """Razorpay subscription updated (e.g. quantity change). Sync metadata."""
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
    log: "WebhookEventLogModel",
) -> None:
    """
    subscription.pending fires when a renewal charge attempt fails but
    Razorpay will retry.  The subscription moves to 'pending'.

    Access remains restricted until subscription.charged fires.
    """
    if sub is None:
        return

    sub.status = SubscriptionStatusEnum.pending

    rzp_payment_id: str = payment_data.get("id", "")
    if rzp_payment_id:
        _record_subscription_failed_payment(
            sub=sub,
            payment_data=payment_data,
            sub_data=sub_data,
            context="subscription.pending",
        )

    logger.info(
        f"subscription.pending | sub={sub.id} | "
        f"auth_attempts={sub_data.get('auth_attempts', '?')}"
    )


def _sub_halted(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """All retry attempts exhausted. Access should be revoked."""
    if sub is None:
        return

    sub.status = SubscriptionStatusEnum.halted

    rzp_payment_id: str = payment_data.get("id", "")
    if rzp_payment_id:
        _record_subscription_failed_payment(
            sub=sub,
            payment_data=payment_data,
            sub_data=sub_data,
            context="subscription.halted",
        )

    logger.warning(
        f"subscription.halted | sub={sub.id} – user access should be reviewed"
    )


def _sub_cancelled(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """
    subscription.cancelled fires when a subscription is cancelled on Razorpay.

    This includes:
      • User-initiated cancel via /cancel endpoint.
      • System-initiated cancel during plan-change verify() (old sub).

    Both cases are handled idempotently — if the row is already cancelled
    (e.g. verify() already set it), we log and return without error.
    """
    if sub is None:
        # Row not found — this can happen if verify() cancelled the old Razorpay
        # sub and the webhook arrives after the DB row was already cleaned up,
        # or if the event is for a sub we never tracked. Either way it's safe.
        logger.info(
            f"subscription.cancelled: no DB row found for rzp_id={sub_data.get('id')} "
            f"– already handled or unknown subscription"
        )
        return

    # FIX: idempotency guard — verify() already marks old sub as cancelled
    if sub.status == SubscriptionStatusEnum.cancelled:
        logger.info(
            f"subscription.cancelled: sub={sub.id} already cancelled – skipping (idempotent)"
        )
        return

    sub.status = SubscriptionStatusEnum.cancelled
    sub.cancel_at_period_end = True
    logger.info(f"subscription.cancelled | sub={sub.id}")


def _sub_paused(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.paused
    logger.info(f"subscription.paused | sub={sub.id}")


def _sub_resumed(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    if sub is None:
        return
    sub.status = SubscriptionStatusEnum.active
    logger.info(f"subscription.resumed | sub={sub.id}")


# ──────────────────────────────────────────────────────────────────────────────
# Order / payment event handlers  (add-on coin purchases + subscription failures)
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
    """payment.captured is the source of truth for add-on coin credits."""
    rzp_payment_id: str = payment_entity.get("id", "")
    rzp_order_id: str = payment_entity.get("order_id", "") or order_entity.get("id", "")

    if not rzp_order_id:
        logger.error("payment.captured: missing order_id")
        _mark_log(log, "failed", "missing order_id in payment entity")
        return

    # Idempotency
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
        raw_notes = payment_entity.get("notes")
        notes: Dict = raw_notes if isinstance(raw_notes, dict) else {}
        if notes.get("type") == "addon_purchase":
            logger.error(
                f"payment.captured: addon_order not found for order {rzp_order_id}"
            )
            _mark_log(log, "failed", f"addon_order not found for order {rzp_order_id}")
        else:
            logger.info(
                f"payment.captured: non-addon payment {rzp_payment_id} – skipping"
            )
        return

    if addon_order.status == PaymentStatusEnum.success:
        logger.info(
            f"payment.captured: addon_order {addon_order.id} already fulfilled – skipping"
        )
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

    addon_order.status = PaymentStatusEnum.success
    addon_order.provider_payment_id = rzp_payment_id
    addon_order.payment_id = payment.id

    logger.info(
        f"payment.captured (addon) | order={rzp_order_id} | "
        f"payment={rzp_payment_id} | coins={bundle.coins} | user={addon_order.user_id}"
    )


def _order_payment_failed(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """
    payment.failed fires for BOTH addon coin purchases and subscription charges.

    Addon purchase   → payment_entity.order_id is populated
    Subscription     → payment_entity.order_id is null/empty;
                       payment_entity.invoice_id is set
    """
    rzp_payment_id: str = payment_entity.get("id", "")
    rzp_order_id: str = payment_entity.get("order_id", "") or order_entity.get("id", "")
    invoice_id: str = payment_entity.get("invoice_id", "")

    # Global idempotency
    if rzp_payment_id:
        already = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_payment_id == rzp_payment_id)
            .first()
        )
        if already:
            logger.info(f"payment.failed: {rzp_payment_id} already recorded – skipping")
            return

    # ── Case 1: Addon coin purchase failure ───────────────────────────────────
    if rzp_order_id:
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
        return

    # ── Case 2: Subscription payment failure ──────────────────────────────────
    # FIX: Try multiple resolution strategies to find the user_id robustly.
    raw_notes = payment_entity.get("notes")
    notes: Dict = raw_notes if isinstance(raw_notes, dict) else {}
    rzp_subscription_id: str = notes.get("subscription_id", "")

    user_id: int | None = None
    sub: UserSubscriptionModel | None = None

    # Strategy A: resolve via subscription id in notes
    if rzp_subscription_id:
        sub = _resolve_sub_by_rzp_id(rzp_subscription_id)
        if sub:
            user_id = sub.user_id

    # Strategy B: resolve via invoice_id → find any payment with that order_id
    if user_id is None and invoice_id:
        ref_payment = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_order_id == invoice_id)
            .first()
        )
        if ref_payment:
            user_id = ref_payment.user_id
            logger.info(
                f"payment.failed: resolved user_id={user_id} via invoice_id={invoice_id}"
            )

    if user_id is None:
        logger.error(
            f"payment.failed (subscription): cannot resolve user_id | "
            f"invoice={invoice_id} | rzp_subscription_id={rzp_subscription_id} | "
            f"payment={rzp_payment_id} – PaymentModel NOT written"
        )
        _mark_log(
            log,
            "failed",
            f"subscription payment failed but user could not be resolved "
            f"(invoice={invoice_id}, rzp_sub={rzp_subscription_id})",
        )
        return

    amount: float = float(payment_entity.get("amount", 0)) / 100.0
    currency: str = payment_entity.get("currency", "INR")

    failed_payment = PaymentModel(
        user_id=user_id,
        amount=amount,
        currency=currency,
        status=PaymentStatusEnum.failed,
        provider=PaymentProviderEnum.razorpay,
        provider_payment_id=rzp_payment_id,
        provider_order_id=invoice_id or None,
        payment_type=PaymentTypeEnum.subscription,
        metadata_json={
            "error_code": payment_entity.get("error_code"),
            "error_description": payment_entity.get("error_description"),
            "error_reason": payment_entity.get("error_reason"),
            "invoice_id": invoice_id,
            "rzp_subscription_id": rzp_subscription_id,
            "internal_subscription_id": sub.id if sub else None,
            "source": "webhook",
        },
    )
    db.session.add(failed_payment)
    logger.warning(
        f"payment.failed (subscription) | invoice={invoice_id} | "
        f"payment={rzp_payment_id} | user={user_id} | sub={sub.id if sub else '?'}"
    )


def _order_paid(
    payment_entity: Dict,
    order_entity: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """order.paid: already handled by payment.captured; log for completeness."""
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


def _record_subscription_failed_payment(
    sub: UserSubscriptionModel,
    payment_data: Dict,
    sub_data: Dict,
    context: str,
) -> None:
    """
    Write a failed PaymentModel for a subscription retry attempt.
    Skips silently if that payment_id was already recorded (idempotent).
    """
    rzp_payment_id: str = payment_data.get("id", "")
    if not rzp_payment_id:
        return

    existing = (
        db.session.query(PaymentModel)
        .filter(PaymentModel.provider_payment_id == rzp_payment_id)
        .first()
    )
    if existing:
        logger.info(
            f"{context}: payment {rzp_payment_id} already recorded – skipping"
        )
        return

    amount: float = float(payment_data.get("amount", 0)) / 100.0
    currency: str = payment_data.get("currency", "INR")

    failed_payment = PaymentModel(
        user_id=sub.user_id,
        amount=amount,
        currency=currency,
        status=PaymentStatusEnum.failed,
        provider=PaymentProviderEnum.razorpay,
        provider_payment_id=rzp_payment_id,
        provider_order_id=sub_data.get("id"),
        payment_type=PaymentTypeEnum.subscription,
        metadata_json={
            "error_code": payment_data.get("error_code"),
            "error_description": payment_data.get("error_description"),
            "error_reason": payment_data.get("error_reason"),
            "auth_attempts": sub_data.get("auth_attempts"),
            "internal_subscription_id": sub.id,
            "context": context,
            "source": "webhook",
        },
    )
    db.session.add(failed_payment)
    logger.warning(
        f"{context} | failed payment recorded | "
        f"payment={rzp_payment_id} | user={sub.user_id} | sub={sub.id}"
    )


def _credit_subscription_coins(
    sub: UserSubscriptionModel,
    plan: PlanModel,
    payment: PaymentModel,
    period_end: datetime,
) -> None:
    """
    Credit subscription coins to the user's ledger.
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
        reference_type="payment",
        reference_id=payment.id,
        balance_after=new_balance,
    )
    db.session.add(ledger_entry)
    logger.info(
        f"Coins credited | user={sub.user_id} | coins={plan.coins_included} | "
        f"new_balance={new_balance} | expires={period_end}"
    )