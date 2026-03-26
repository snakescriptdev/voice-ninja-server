"""
razorpay_webhook.py
────────────────────────────────────────────────────────────────────────────────
Production-grade Razorpay webhook handler.

Status transition map (immediate subscriptions only):
  subscription.activated  → active   (NOT authenticated — immediate subs only)
  subscription.charged    → active   (money captured — SOLE place for promotion)
  subscription.completed  → completed
  subscription.pending    → pending
  subscription.halted     → halted
  subscription.cancelled  → cancelled
  subscription.paused     → paused
  subscription.resumed    → active

Race-condition design:
  subscription.charged is self-healing:
    • If no DB row exists yet (webhook beat verify()), it creates one via
      _upsert_subscription_row() and credits coins.
    • If a row already exists (verify() ran first), it updates it to active
      and credits coins only if not already credited.

  subscription.activated NEVER creates rows:
    • Creating rows in activated caused the duplicate-row bug (activated and
      verify() both created rows, wrong one won the ordering tiebreaker).
    • For immediate subscriptions activated fires right before or after charged.
      charged is always the authoritative handler.

  Both verify() and _sub_charged use _upsert_subscription_row() so whichever
  arrives first wins and the other safely updates in place.

Coin credit:
  Coins are credited in verify() (immediate, on payment confirmation) AND
  guarded in _sub_charged (in case webhook arrives before verify commits).
  The ledger idempotency check in both places prevents double-credit regardless
  of ordering.

Covered events:
  Subscription: activated, charged, completed, updated, pending,
                halted, cancelled, paused, resumed
  Order/payment: payment.captured, payment.failed, order.paid
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone, timedelta
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
    if ts is None:
        return None
    return datetime.utcfromtimestamp(ts)


def _calc_period_end(plan: PlanModel, start: datetime) -> datetime:
    if plan.billing_period.value == "annual":
        return start + timedelta(days=365)
    return start + timedelta(days=30)


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


def _resolve_sub_by_rzp_id(rzp_subscription_id: str) -> "UserSubscriptionModel | None":
    """
    Look up subscription row by Razorpay subscription id across ALL statuses.

    Searching all statuses ensures:
      - subscription.cancelled for an already-cancelled row is handled idempotently.
      - subscription.charged for a newly-created row is found even before
        verify() has promoted status to active.
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(UserSubscriptionModel.provider_subscription_id == rzp_subscription_id)
        .first()
    )


def _upsert_subscription_row(
    *,
    rzp_subscription_id: str,
    user_id: int,
    plan: PlanModel,
    current_start: datetime,
    current_end: datetime,
    extra_fields: dict = None,
) -> "UserSubscriptionModel":
    """
    Find an existing row by provider_subscription_id and update it to active,
    OR create a fresh row if none exists.

    Shared between verify() and _sub_charged so whichever arrives first wins,
    and the other safely updates in place without creating duplicates.
    """
    sub = _resolve_sub_by_rzp_id(rzp_subscription_id)

    if sub is not None:
        logger.info(
            f"_upsert_subscription_row: found existing row id={sub.id} for "
            f"rzp_id={rzp_subscription_id} — updating to active"
        )
        sub.status = SubscriptionStatusEnum.active
        sub.plan_id = plan.id
        sub.current_period_start = current_start
        sub.current_period_end = current_end
        sub.cancel_at_period_end = False
        sub.pending_provider_subscription_id = None
        sub.next_plan_id = None
        if extra_fields:
            for k, v in extra_fields.items():
                setattr(sub, k, v)
    else:
        logger.info(
            f"_upsert_subscription_row: creating new row for "
            f"rzp_id={rzp_subscription_id} user={user_id} plan={plan.id}"
        )
        kwargs = dict(
            user_id=user_id,
            plan_id=plan.id,
            status=SubscriptionStatusEnum.active,
            current_period_start=current_start,
            current_period_end=current_end,
            cancel_at_period_end=False,
            provider="razorpay",
            provider_subscription_id=rzp_subscription_id,
            pending_provider_subscription_id=None,
            next_plan_id=None,
        )
        if extra_fields:
            kwargs.update(extra_fields)
        sub = UserSubscriptionModel(**kwargs)
        db.session.add(sub)

    db.session.flush()
    return sub


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
        # Do NOT re-raise — return 200 so Razorpay doesn't retry infinitely.

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
    subscription.activated fires when the mandate is confirmed and the first
    payment is authorised.

    For IMMEDIATE subscriptions (our only supported mode), activated fires
    at roughly the same time as charged. We treat it as a soft confirmation:

      • If the row already exists and is active → no-op (charged already ran).
      • If the row already exists and is not active → set to active.
      • If no row exists → DO NOT create one. charged will create it.
        Creating a row here caused the duplicate-row race condition where
        activated and verify() both inserted rows and the wrong one won
        the created_at ordering tiebreaker.
    """
    if sub is None:
        # Do NOT create a skeleton row here.
        # subscription.charged will create the row (self-healing) if verify()
        # hasn't committed yet. Creating it here produces duplicate rows.
        logger.info(
            f"subscription.activated: no DB row yet for rzp_id={sub_data.get('id')} "
            f"— skipping row creation (subscription.charged will handle it)"
        )
        return

    # Row exists — ensure it's active. Guard against downgrading from active.
    if sub.status not in (SubscriptionStatusEnum.active, SubscriptionStatusEnum.paused):
        sub.status = SubscriptionStatusEnum.active
        logger.info(
            f"subscription.activated | rzp_id={sub_data['id']} | "
            f"status promoted to active"
        )
    else:
        logger.info(
            f"subscription.activated | rzp_id={sub_data['id']} | "
            f"already {sub.status} — no status change"
        )

    sub.cancel_at_period_end = False


def _sub_charged(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
    """
    subscription.charged fires each billing cycle after a successful charge.

    This is THE canonical place to:
      • Upsert the subscription row to active (self-healing if verify() hasn't
        committed yet — webhook beat verify race).
      • Credit coins for the cycle (guarded by ledger idempotency check so
        verify()-credited coins are never doubled).
      • Record the payment.

    Handles both first-charge and recurring charges.
    """
    rzp_payment_id: str = payment_data.get("id", "")
    amount: float = float(payment_data.get("amount", 0)) / 100.0
    currency: str = payment_data.get("currency", "INR")

    new_start = _ts_to_dt(sub_data.get("current_start")) or datetime.now(timezone.utc)

    # ── Resolve plan ──────────────────────────────────────────────────────────
    # If sub already exists, use its plan_id (handles plan-change path).
    # If not, resolve from notes (webhook-beat-verify path).
    plan: PlanModel | None = None
    user_id: int | None = None

    if sub is not None:
        plan = db.session.query(PlanModel).filter(PlanModel.id == sub.plan_id).first()
        user_id = sub.user_id
    else:
        notes: Dict = sub_data.get("notes", {}) or {}
        user_id = int(notes.get("user_id", 0)) or None
        plan_id = int(notes.get("plan_id", 0)) or None
        if user_id and plan_id:
            plan = db.session.query(PlanModel).filter(PlanModel.id == plan_id).first()

    if not plan or not user_id:
        logger.error(
            f"subscription.charged: cannot resolve plan/user for "
            f"rzp_id={sub_data.get('id')} sub={'exists' if sub else 'missing'}"
        )
        _mark_log(log, "failed", "cannot resolve plan or user_id")
        return

    new_end = _ts_to_dt(sub_data.get("current_end")) or _calc_period_end(plan, new_start)

    # ── Idempotency: check if coins already credited for this payment ─────────
    # verify() may have already created a PaymentModel AND credited coins.
    # Only skip entirely if a ledger entry exists — not just a PaymentModel.
    payment: PaymentModel | None = None
    coins_already_credited = False

    if rzp_payment_id:
        existing_payment = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_payment_id == rzp_payment_id)
            .first()
        )
        if existing_payment:
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
                    f"subscription.charged | payment={rzp_payment_id} already has "
                    f"ledger entry — coins already credited, skipping"
                )
                coins_already_credited = True
                # Still upsert the row to active in case verify() didn't commit yet.
                payment = existing_payment
            else:
                logger.info(
                    f"subscription.charged | payment={rzp_payment_id} exists but "
                    f"no coins credited yet — proceeding"
                )
                payment = existing_payment

    # ── Upsert subscription row to active ─────────────────────────────────────
    # Self-healing: creates the row if verify() hasn't committed yet.
    # Updates in place if verify() already created it.
    sub = _upsert_subscription_row(
        rzp_subscription_id=sub_data["id"],
        user_id=user_id,
        plan=plan,
        current_start=new_start,
        current_end=new_end,
        extra_fields={
            "subscription_metadata": {"customer_id": sub_data.get("customer_id", "")}
        } if sub is None else None,
    )

    # Handle pending plan change safety-net: if next_plan_id still set
    # (normally cleared by verify), swap the plan now so the webhook is
    # self-healing for missed verify() calls.
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
                        schedule_downgrade_for_user(
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

    # ── Record payment if not already recorded ────────────────────────────────
    if payment is None and rzp_payment_id:
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
    elif payment is not None:
        # Payment exists (from verify) — ensure it's marked success.
        payment.status = PaymentStatusEnum.success
        db.session.flush()

    # ── Credit coins (only if not already credited) ───────────────────────────
    if not coins_already_credited and payment is not None:
        _credit_subscription_coins(sub, plan, payment, new_end)

    logger.info(
        f"subscription.charged | sub={sub.id} | payment={rzp_payment_id} | "
        f"plan={plan.id} | status=active | coins_credited={not coins_already_credited}"
    )


def _sub_completed(
    sub: UserSubscriptionModel | None,
    sub_data: Dict,
    payment_data: Dict,
    log: "WebhookEventLogModel",
) -> None:
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
    Renewal charge failed — Razorpay will retry.
    Access remains until subscription.charged fires (or halted if all retries fail).
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
    subscription.cancelled fires for both user-initiated and system-initiated
    (plan-change verify()) cancellations. Both are handled idempotently.
    """
    if sub is None:
        logger.info(
            f"subscription.cancelled: no DB row found for rzp_id={sub_data.get('id')} "
            f"— already handled or unknown subscription"
        )
        return

    if sub.status == SubscriptionStatusEnum.cancelled:
        logger.info(
            f"subscription.cancelled: sub={sub.id} already cancelled — skipping (idempotent)"
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
    """payment.captured is the source of truth for add-on coin credits."""
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
        expiry_date = datetime.now(timezone.utc) + timedelta(days=bundle.validity_days)

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
    payment.failed fires for both addon coin purchases and subscription charges.

    Addon purchase   → payment_entity.order_id is populated
    Subscription     → payment_entity.order_id is null/empty;
                       payment_entity.invoice_id is set
    """
    rzp_payment_id: str = payment_entity.get("id", "")
    rzp_order_id: str = payment_entity.get("order_id", "") or order_entity.get("id", "")
    invoice_id: str = payment_entity.get("invoice_id", "")

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
    raw_notes = payment_entity.get("notes")
    notes: Dict = raw_notes if isinstance(raw_notes, dict) else {}
    rzp_subscription_id: str = notes.get("subscription_id", "")

    user_id: int | None = None
    sub: UserSubscriptionModel | None = None

    if rzp_subscription_id:
        sub = _resolve_sub_by_rzp_id(rzp_subscription_id)
        if sub:
            user_id = sub.user_id

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
    rzp_order_id: str = order_entity.get("id", "")
    logger.info(f"order.paid | order={rzp_order_id} – already handled by payment.captured")


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _record_subscription_failed_payment(
    sub: UserSubscriptionModel,
    payment_data: Dict,
    sub_data: Dict,
    context: str,
) -> None:
    """
    Write a failed PaymentModel for a subscription retry attempt.
    Idempotent — skips silently if that payment_id was already recorded.
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
        logger.info(f"{context}: payment {rzp_payment_id} already recorded – skipping")
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
    """Credit subscription coins. Called from both verify() and _sub_charged."""
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