"""
subscriptions.py
────────────────────────────────────────────────────────────────────────────────
Subscription lifecycle:

  CREATE   → Razorpay subscription object created.  No DB row yet.

  UPDATE   → New Razorpay subscription created on the new plan.
               • Old Razorpay subscription is NOT cancelled here.
               • Existing DB row stamped with pending_provider_subscription_id.
               • cancel_at_period_end is NOT touched — user retains full access
                 during checkout.

  CANCEL-PENDING  → User aborts an in-progress plan-change checkout.
               • New (incomplete) Razorpay sub cancelled immediately.
               • pending_provider_subscription_id cleared from existing DB row.

  VERIFY   → Frontend calls after checkout completes.
               PATH A (new sub):
                 • Upsert: if webhook already created the row, update it.
                   Otherwise create a fresh row.
                 • status = active  (immediate subscriptions only).
               PATH B (plan change):
                 • Old Razorpay subscription cancelled immediately.
                 • Old DB row marked cancelled.
                 • Upsert new row: if webhook already created it, update it.
                   Otherwise create fresh row.
                 • status = active.
               • Coins ARE credited here (immediate subscriptions).
               • webhook subscription.charged is idempotent — won't double-credit.

  WEBHOOK  subscription.charged
               → Upsert row if not yet created by verify() (webhook beat verify).
               → status = active.
               → Coins credited only if not already credited for this payment.

  WEBHOOK  subscription.activated
               → NEVER creates rows (avoids duplicate-row race).
               → If row exists and status is not already active/paused,
                 set status = active  (immediate subscriptions — no authenticated state).

Why no authenticated state:
  We only support immediate subscriptions where the first charge is captured
  synchronously with checkout completion. status goes directly to active.
  authenticated is kept in _ACTIVE_LIKE for safety but is never intentionally set.

Race-condition design:
  verify() does ALL external API calls (cancel old sub, fetch dates, fetch invoice)
  BEFORE opening the DB write section. This minimises the window during which
  webhooks can race with an open transaction.

  Both verify() and webhook handlers upsert rows instead of blindly inserting,
  so whichever arrives first wins and the other safely updates in place.

  subscription.charged is self-healing: if it arrives before verify() has committed
  it creates the row itself. verify() will then find and update it.

  subscription.activated never creates rows, so it cannot produce duplicates.

Single-row-per-user invariant:
  At any point there is at most one row with status in (active, paused) and
  cancel_at_period_end=False. After a plan change verify(), the old row is marked
  cancelled and a new row (or webhook-created row) holds the new plan.

Migration notes:
  ALTER TABLE user_subscriptions
    ADD COLUMN IF NOT EXISTS pending_provider_subscription_id VARCHAR(255);
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from sqlalchemy import or_
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer
from app_v2.databases.models import (
    UnifiedAuthModel, PlanModel, UserSubscriptionModel,
    PaymentModel, PlanProviderModel, CoinsLedgerModel,
)
from app_v2.schemas.subscriptions import (
    SubscriptionCreate, SubscriptionResponse, SubscriptionVerifyRequest,
    SubscriptionCancelRequest, SubscriptionUpdateRequest, SubscriptionPauseRequest,
    InvoiceListResponse, InvoiceItemResponse,
)
from app_v2.schemas.enum_types import (
    PaymentProviderEnum, SubscriptionStatusEnum,
    PaymentStatusEnum, PaymentTypeEnum, CoinTransactionTypeEnum,
)
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timezone, timedelta
from app_v2.utils.coin_utils import get_user_coin_balance, reset_unused_subscription_coins
from fastapi.responses import HTMLResponse
import os
from app_v2.utils.time_utils import convert_to_unix_timestamp
from app_v2.schemas.enum_types import ScheduledDowngradeTriggerEnum


logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/subscriptions", tags=["Subscriptions"])


# ──────────────────────────────────────────────────────────────────────────────
# Active-like statuses  (kept in sync with feature_access.py)
# authenticated is included for safety but is never intentionally assigned.
# ──────────────────────────────────────────────────────────────────────────────

_ACTIVE_LIKE = (
    SubscriptionStatusEnum.active,
    SubscriptionStatusEnum.paused,
    SubscriptionStatusEnum.authenticated,
)


@router.get("/demo", response_class=HTMLResponse)
async def get_subscription_demo():
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_subscription.html")
    with open(template_path, "r") as f:
        return f.read()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_current_subscription(user_id: int) -> "UserSubscriptionModel | None":
    """
    Return the single canonical active/paused subscription for a user.

    Rules:
      • status must be active, paused, or authenticated
      • cancel_at_period_end must be False
      • order by created_at desc as tiebreaker
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.status.in_(_ACTIVE_LIKE),
            UserSubscriptionModel.cancel_at_period_end == False,
        )
        .order_by(UserSubscriptionModel.created_at.desc())
        .first()
    )


def _get_any_subscription_with_pending(user_id: int) -> "UserSubscriptionModel | None":
    """
    Return any subscription row for this user that has a pending plan-change
    in flight (pending_provider_subscription_id is set).

    Used by verify() and cancel-pending-update().
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.pending_provider_subscription_id.isnot(None),
        )
        .first()
    )


def _calc_period_end(plan: PlanModel, start: datetime) -> datetime:
    if plan.billing_period.value == "annual":
        return start + timedelta(days=365)
    return start + timedelta(days=30)


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

    This is the core of the race-condition fix: both verify() and
    subscription.charged call this so whichever arrives first wins,
    and the second one safely updates in place without creating a duplicate.
    """
    sub = (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.provider_subscription_id == rzp_subscription_id
        )
        .first()
    )

    if sub is not None:
        # Row already exists (webhook beat verify, or verify beat webhook and
        # webhook is now updating). Bring it to the correct final state.
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
        # No row yet — create it.
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
# Create subscription
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/create",
    response_model=SubscriptionResponse,
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def create_subscription(
    data: SubscriptionCreate,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Initiate a brand-new Razorpay subscription (first-time or after expiry/cancel).

    Blocked if:
      • User already has an active/paused subscription with cancel_at_period_end=False
      • User already has an update in-flight (pending_provider_subscription_id set)
    """
    try:
        active_sub = _get_current_subscription(current_user.id)
        if active_sub:
            raise HTTPException(
                status_code=400,
                detail="You already have an active subscription. "
                       "Please update or cancel it first.",
            )

        pending_sub = _get_any_subscription_with_pending(current_user.id)
        if pending_sub:
            raise HTTPException(
                status_code=400,
                detail="A plan change is already in progress. "
                       "Please complete or cancel the pending checkout first.",
            )

        plan = (
            db.session.query(PlanModel)
            .filter(PlanModel.id == data.plan_id, PlanModel.is_active == True)
            .first()
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found or inactive")

        provider_plan = (
            db.session.query(PlanProviderModel)
            .filter(
                PlanProviderModel.plan_id == plan.id,
                PlanProviderModel.provider == PaymentProviderEnum.razorpay,
                PlanProviderModel.is_active == True,
            )
            .first()
        )
        if not provider_plan:
            raise HTTPException(status_code=400, detail="Razorpay plan not configured for this plan")

        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        subscription = rzp_provider.create_subscription(
            plan_id=provider_plan.provider_plan_id,
            notes={
                "user_id": str(current_user.id),
                "plan_id": str(plan.id),
            },
        )

        return SubscriptionResponse(
            subscription_id=subscription["id"],
            amount=plan.price,
            currency=plan.currency,
            plan_id=plan.id,
            plan_name=plan.display_name,
            user_email=current_user.email,
            user_phone=current_user.phone,
            key_id=VoiceSettings.RAZOR_KEY_ID,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Verify  (called by frontend after checkout — handles BOTH new & plan-change)
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/verify",
    openapi_extra={"security": [{"BearerAuth": []}]},
    dependencies=[Depends(security)],
)
def verify_subscription(
    data: SubscriptionVerifyRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Called by the frontend after Razorpay checkout completes.

    Two paths:
      A) NEW subscription
         • Upsert row with status=active (handles webhook-beat-verify race).
         • Credit coins.

      B) PLAN CHANGE — existing row has pending_provider_subscription_id matching
         data.razorpay_subscription_id.
         • Old Razorpay subscription cancelled (external call done BEFORE DB writes).
         • Old DB row marked cancelled.
         • Upsert new row with status=active.
         • Credit coins.

    Race-condition safety:
      All external API calls happen BEFORE any DB writes. This minimises the
      window during which subscription.charged / subscription.activated webhooks
      can race with an open transaction.

      _upsert_subscription_row() is used instead of a blind INSERT, so if the
      webhook already created the row verify() updates it in place rather than
      creating a duplicate.

    Idempotent: if provider_subscription_id already exists on a row AND a ledger
    entry is already recorded for this payment, return success immediately.
    """
    try:
        # ── Idempotency: fully processed already ──────────────────────────────
        existing_sub = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.provider_subscription_id == data.razorpay_subscription_id
            )
            .first()
        )
        if existing_sub:
            existing_payment = (
                db.session.query(PaymentModel)
                .filter(PaymentModel.provider_payment_id == data.razorpay_payment_id)
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
                        f"verify_subscription: already fully processed "
                        f"{data.razorpay_subscription_id} — returning early"
                    )
                    return {"message": "Subscription already verified"}

        # ════════════════════════════════════════════════════════════════════
        # PHASE 1 — All external API calls BEFORE any DB writes.
        # This keeps the DB transaction window as short as possible and
        # prevents webhooks from racing with a long-held open transaction.
        # ════════════════════════════════════════════════════════════════════

        # ── 1a. Signature verification ────────────────────────────────────────
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        params = {
            "razorpay_payment_id": data.razorpay_payment_id,
            "razorpay_subscription_id": data.razorpay_subscription_id,
            "razorpay_signature": data.razorpay_signature,
        }
        if not rzp_provider.verify_payment_signature(params):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        # ── 1b. Fetch plan ────────────────────────────────────────────────────
        plan = (
            db.session.query(PlanModel)
            .filter(PlanModel.id == data.plan_id, PlanModel.is_active == True)
            .first()
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")

        # ── 1c. Fetch period dates from Razorpay ──────────────────────────────
        current_start = datetime.now(timezone.utc)
        current_end = _calc_period_end(plan, current_start)
        try:
            rzp_sub = rzp_provider.get_subscription_details(data.razorpay_subscription_id)
            if rzp_sub.get("current_start"):
                current_start = datetime.utcfromtimestamp(rzp_sub["current_start"])
            if rzp_sub.get("current_end"):
                current_end = datetime.utcfromtimestamp(rzp_sub["current_end"])
        except Exception as e:
            logger.warning(
                f"verify_subscription: could not fetch RZP subscription details: {e} "
                f"— using fallback dates"
            )

        # ── 1d. Detect plan-change vs new subscription ────────────────────────
        pending_sub = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.user_id == current_user.id,
                UserSubscriptionModel.pending_provider_subscription_id == data.razorpay_subscription_id,
            )
            .first()
        )
        is_plan_change = pending_sub is not None

        if not is_plan_change:
            stale_pending = _get_any_subscription_with_pending(current_user.id)
            if stale_pending:
                logger.warning(
                    f"verify_subscription: stale pending_provider_subscription_id="
                    f"{stale_pending.pending_provider_subscription_id} on sub={stale_pending.id} "
                    f"does not match incoming rzp_sub={data.razorpay_subscription_id} — "
                    f"treating as new subscription. Orphaned RZP sub may need manual cancellation."
                )

        # ── 1e. Cancel old Razorpay sub NOW (plan-change only, BEFORE DB writes) ──
        # Doing this before DB writes means the DB transaction window is short.
        # If this call fails it is non-fatal — ops can cancel manually.
        old_rzp_sub_id: str | None = None
        old_plan_id: int | None = None
        old_period_end: datetime | None = None

        if is_plan_change:
            old_rzp_sub_id = pending_sub.provider_subscription_id
            old_plan_id = pending_sub.plan_id
            old_period_end = pending_sub.current_period_end
            try:
                rzp_provider.cancel_subscription(
                    subscription_id=old_rzp_sub_id,
                    cancel_at_cycle_end=False,
                )
                logger.info(
                    f"verify_subscription | old Razorpay sub cancelled (pre-DB) | "
                    f"old_rzp_id={old_rzp_sub_id}"
                )
            except Exception as cancel_err:
                logger.error(
                    f"verify_subscription | FAILED to cancel old Razorpay sub "
                    f"{old_rzp_sub_id} — manual intervention may be required | "
                    f"error={cancel_err}"
                )

        # ── 1f. Fetch invoice URL (non-fatal) ─────────────────────────────────
        invoice_url: str | None = None
        try:
            invoices = rzp_provider.get_subscription_invoices(data.razorpay_subscription_id)
            if invoices:
                invoice_url = invoices[0].get("short_url") or invoices[0].get("invoice_url")
        except Exception as inv_err:
            logger.warning(f"verify_subscription: could not fetch invoice: {inv_err}")

        # ════════════════════════════════════════════════════════════════════
        # PHASE 2 — DB writes only. Transaction is as short as possible.
        # ════════════════════════════════════════════════════════════════════

        downgrade_summary: dict = {}

        if is_plan_change:
            logger.info(
                f"verify_subscription: plan-change path | "
                f"old_sub_id={pending_sub.id} | "
                f"old_plan={old_plan_id} → new_plan={plan.id}"
            )

            # Mark old subscription cancelled in DB.
            pending_sub.status = SubscriptionStatusEnum.cancelled
            pending_sub.cancel_at_period_end = True
            pending_sub.pending_provider_subscription_id = None
            pending_sub.next_plan_id = None
            # Keep provider_subscription_id so subscription.cancelled webhook
            # can resolve and no-op it cleanly.
            db.session.flush()

            # Upsert new subscription row (handles webhook-beat-verify race).
            subscription = _upsert_subscription_row(
                rzp_subscription_id=data.razorpay_subscription_id,
                user_id=current_user.id,
                plan=plan,
                current_start=current_start,
                current_end=current_end,
            )

            # Deferred downgrade enforcement.
            from app_v2.utils.downgrade_utils import (
                schedule_downgrade_for_user,
                compute_downgrade_diff,
            )
            from app_v2.utils.activity_logger import log_activity

            downgrade_diff = compute_downgrade_diff(old_plan_id, plan.id, db.session)

            if downgrade_diff:
                schedule_downgrade_for_user(
                    user_id=current_user.id,
                    old_plan_id=old_plan_id,
                    new_plan_id=plan.id,
                    subscription_id=subscription.id,
                    scheduled_for=old_period_end,
                    trigger_source=ScheduledDowngradeTriggerEnum.plan_change,
                    session=db.session,
                )
                log_activity(
                    user_id=current_user.id,
                    event_type="subscription_downgrade_scheduled",
                    description=(
                        f"Plan downgrade scheduled from {old_plan_id} to {plan.id} "
                        f"on {old_period_end.strftime('%Y-%m-%d')}."
                    ),
                    metadata={
                        "old_plan_id": old_plan_id,
                        "new_plan_id": plan.id,
                        "scheduled_for": old_period_end.isoformat(),
                        "diff": downgrade_diff,
                    },
                )
                logger.info(
                    f"verify_subscription | downgrade scheduled | "
                    f"user={current_user.id} | scheduled_for={old_period_end}"
                )
                downgrade_summary = {
                    "scheduled_for": old_period_end.strftime('%Y-%m-%d'),
                    "affected_features": downgrade_diff,
                }
            db.session.flush()

        else:
            # PATH A: New subscription.
            logger.info(
                f"verify_subscription: new subscription path | "
                f"user={current_user.id} | plan={plan.id}"
            )
            subscription = _upsert_subscription_row(
                rzp_subscription_id=data.razorpay_subscription_id,
                user_id=current_user.id,
                plan=plan,
                current_start=current_start,
                current_end=current_end,
            )

            # Cancel any pending downgrades — user is on a new plan now.
            from app_v2.utils.downgrade_utils import cancel_scheduled_downgrade_for_user
            cancel_scheduled_downgrade_for_user(current_user.id, db.session)

        # ── Record payment & credit coins ─────────────────────────────────────
        # Guard: only credit coins if no ledger entry exists for this payment yet.
        # This prevents double-credit if subscription.charged already ran.
        existing_payment = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_payment_id == data.razorpay_payment_id)
            .first()
        )

        coins_already_credited = False
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
                coins_already_credited = True
                logger.info(
                    f"verify_subscription: coins already credited by webhook for "
                    f"payment={data.razorpay_payment_id} — skipping credit"
                )

        if not coins_already_credited:
            if not existing_payment:
                cycle_label = "plan_change" if is_plan_change else "first"
                payment = PaymentModel(
                    user_id=current_user.id,
                    amount=plan.price,
                    currency=plan.currency,
                    status=PaymentStatusEnum.success,
                    provider=PaymentProviderEnum.razorpay,
                    provider_payment_id=data.razorpay_payment_id,
                    provider_order_id=data.razorpay_subscription_id,
                    payment_type=PaymentTypeEnum.subscription,
                    invoice_url=invoice_url,
                    metadata_json={
                        "plan_id": plan.id,
                        "subscription_id": subscription.id,
                        "cycle": cycle_label,
                    },
                )
                db.session.add(payment)
                db.session.flush()
            else:
                payment = existing_payment
                # Ensure payment is linked to the correct subscription.
                if payment.invoice_url is None and invoice_url:
                    payment.invoice_url = invoice_url

            # Credit coins.
            if not plan.carry_forward_coins:
                reset_unused_subscription_coins(subscription.user_id)

            current_balance = get_user_coin_balance(subscription.user_id)
            new_balance = current_balance + plan.coins_included

            ledger_entry = CoinsLedgerModel(
                user_id=subscription.user_id,
                transaction_type=CoinTransactionTypeEnum.credit_subscription,
                coins=plan.coins_included,
                remaining_coins=plan.coins_included,
                expiry_at=current_end,
                reference_type="payment",
                reference_id=payment.id,
                balance_after=new_balance,
            )
            db.session.add(ledger_entry)
            logger.info(
                f"verify_subscription | coins credited | "
                f"user={subscription.user_id} | coins={plan.coins_included}"
            )

        db.session.commit()

        msg = (
            f"Plan change activated. Downgrade will be enforced on "
            f"{old_period_end.strftime('%Y-%m-%d')}."
            if is_plan_change and downgrade_summary
            else (
                "Plan change activated successfully."
                if is_plan_change
                else "Subscription activated successfully."
            )
        )
        return {
            "message": msg,
            "downgrade_notice": downgrade_summary if (is_plan_change and downgrade_summary) else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error verifying subscription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Cancel pending plan-change
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/cancel-pending-update",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def cancel_pending_update(
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Abort an in-progress plan-change checkout.

    Call this when the user closes or cancels the Razorpay checkout modal
    without completing payment. Cancels the incomplete new Razorpay subscription
    and clears the pending state from the existing DB row.

    The user's current subscription is unaffected.
    """
    try:
        pending_sub = _get_any_subscription_with_pending(current_user.id)
        if not pending_sub:
            raise HTTPException(
                status_code=404,
                detail="No pending plan change found for this account.",
            )

        new_rzp_sub_id: str = pending_sub.pending_provider_subscription_id

        try:
            rzp = PaymentProviderFactory.get_provider(pending_sub.provider)
            rzp.cancel_subscription(
                subscription_id=new_rzp_sub_id,
                cancel_at_cycle_end=False,
            )
            logger.info(
                f"cancel_pending_update | new RZP sub {new_rzp_sub_id} cancelled | "
                f"user={current_user.id}"
            )
        except Exception as rzp_err:
            logger.warning(
                f"cancel_pending_update | could not cancel RZP sub {new_rzp_sub_id} | "
                f"error={rzp_err} — proceeding with DB cleanup"
            )

        pending_sub.pending_provider_subscription_id = None
        pending_sub.next_plan_id = None

        db.session.commit()

        logger.info(
            f"cancel_pending_update | sub={pending_sub.id} cleaned up | user={current_user.id}"
        )
        return {"message": "Pending plan change cancelled. Your current subscription remains active."}

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error cancelling pending update: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Cancel
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/cancel",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def cancel_subscription(
    data: SubscriptionCancelRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Cancel the user's active subscription.

    cancel_at_cycle_end=True  → access continues until period end (recommended).
    cancel_at_cycle_end=False → immediate cancellation.
    """
    try:
        subscription = _get_current_subscription(current_user.id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

        provider = PaymentProviderFactory.get_provider(subscription.provider)
        response = provider.cancel_subscription(
            subscription.provider_subscription_id,
            data.cancel_at_cycle_end,
        )

        if data.cancel_at_cycle_end:
            subscription.cancel_at_period_end = True
        else:
            subscription.status = SubscriptionStatusEnum.cancelled
            subscription.cancel_at_period_end = True

        if subscription.subscription_metadata is None:
            subscription.subscription_metadata = {}
        subscription.subscription_metadata["customer_id"] = response.get("customer_id", "")

        db.session.commit()
        return {
            "message": "Subscription cancellation initiated",
            "cancel_at_period_end": data.cancel_at_cycle_end,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error cancelling subscription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Update  (plan change — kicks off new Razorpay subscription checkout)
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/update",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def update_subscription(
    data: SubscriptionUpdateRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Initiate a plan change.

    Flow:
      1. Create a new Razorpay subscription on the new plan.
         Old Razorpay subscription is NOT touched here.
      2. Stamp pending_provider_subscription_id + next_plan_id on the existing
         DB row. cancel_at_period_end is NOT changed — user's current subscription
         stays fully active if they abandon the checkout.
      3. If a previous checkout was abandoned (stale pending id), silently cancel
         the orphaned Razorpay subscription and overwrite.
      4. verify() cancels old Razorpay sub, marks old row cancelled, upserts new row.
    """
    try:
        subscription = _get_current_subscription(current_user.id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

        if subscription.plan_id == data.plan_id:
            raise HTTPException(status_code=400, detail="You are already on this plan")

        # If a previous checkout was abandoned, silently cancel orphaned RZP sub.
        if subscription.pending_provider_subscription_id:
            stale_rzp_sub_id = subscription.pending_provider_subscription_id
            try:
                rzp_provider_cleanup = PaymentProviderFactory.get_provider(subscription.provider)
                rzp_provider_cleanup.cancel_subscription(
                    subscription_id=stale_rzp_sub_id,
                    cancel_at_cycle_end=False,
                )
                logger.info(
                    f"update_subscription | cancelled stale pending RZP sub {stale_rzp_sub_id} "
                    f"before creating new one | user={current_user.id}"
                )
            except Exception as cleanup_err:
                logger.warning(
                    f"update_subscription | could not cancel stale pending RZP sub "
                    f"{stale_rzp_sub_id} | error={cleanup_err} — overwriting anyway"
                )
            subscription.pending_provider_subscription_id = None
            subscription.next_plan_id = None

        new_plan = (
            db.session.query(PlanModel)
            .filter(PlanModel.id == data.plan_id, PlanModel.is_active == True)
            .first()
        )
        if not new_plan:
            raise HTTPException(status_code=404, detail="New plan not found or inactive")

        provider_plan = (
            db.session.query(PlanProviderModel)
            .filter(
                PlanProviderModel.plan_id == new_plan.id,
                PlanProviderModel.provider == subscription.provider,
                PlanProviderModel.is_active == True,
            )
            .first()
        )
        if not provider_plan:
            raise HTTPException(
                status_code=400,
                detail=f"Provider plan not configured for {subscription.provider}",
            )

        old_plan = db.session.query(PlanModel).filter(PlanModel.id == subscription.plan_id).first()
        is_downgrade = (old_plan is not None) and (new_plan.price < old_plan.price)

        rzp_provider = PaymentProviderFactory.get_provider(subscription.provider)
        new_rzp_subscription = rzp_provider.create_subscription(
            plan_id=provider_plan.provider_plan_id,
            notes={
                "user_id": str(current_user.id),
                "plan_id": str(new_plan.id),
            },
        )
        new_rzp_sub_id: str = new_rzp_subscription["id"]

        subscription.pending_provider_subscription_id = new_rzp_sub_id
        subscription.next_plan_id = new_plan.id

        if subscription.subscription_metadata is None:
            subscription.subscription_metadata = {}
        subscription.subscription_metadata["pending_razorpay_subscription_id"] = new_rzp_sub_id

        db.session.commit()
        logger.info(
            f"update_subscription | sub={subscription.id} | "
            f"old_plan={subscription.plan_id} → new_plan={new_plan.id} | "
            f"pending_rzp_id={new_rzp_sub_id}"
        )

        return {
            "message": "Plan change initiated. Complete payment for the new plan.",
            "subscription_id": new_rzp_sub_id,
            "amount": new_plan.price,
            "currency": new_plan.currency,
            "plan_id": new_plan.id,
            "plan_name": new_plan.display_name,
            "user_email": current_user.email,
            "user_phone": current_user.phone,
            "key_id": VoiceSettings.RAZOR_KEY_ID,
            "is_downgrade": is_downgrade,
            "downgrade_warning": (
                "Some resources will be automatically adjusted when payment completes."
                if is_downgrade else None
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating subscription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Downgrade preview
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/downgrade-preview",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def downgrade_preview(
    plan_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Returns a dry-run summary of what will be auto-disabled if the user
    downgrades to the given plan. Purely informational — makes NO changes.
    """
    try:
        from app_v2.utils.downgrade_utils import compute_downgrade_preview

        subscription = _get_current_subscription(current_user.id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

        if subscription.plan_id == plan_id:
            return {"is_downgrade": False, "affected_features": {}}

        preview = compute_downgrade_preview(
            user_id=current_user.id,
            old_plan_id=subscription.plan_id,
            new_plan_id=plan_id,
            session=db.session,
        )

        if preview.get("is_downgrade"):
            preview["message"] = (
                f"Downgrade will be enforced on "
                f"{subscription.current_period_end.strftime('%Y-%m-%d')}."
            )
            preview["enforcement_date"] = subscription.current_period_end.strftime('%Y-%m-%d')

        return preview

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing downgrade preview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Pause / Resume
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/pause",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def pause_subscription(
    data: SubscriptionPauseRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    try:
        subscription = _get_current_subscription(current_user.id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")
        if subscription.status == SubscriptionStatusEnum.paused:
            raise HTTPException(status_code=400, detail="Subscription is already paused")

        provider = PaymentProviderFactory.get_provider(subscription.provider)
        provider.pause_subscription(subscription.provider_subscription_id, data.pause_at)

        if data.pause_at == "now":
            subscription.status = SubscriptionStatusEnum.paused

        db.session.commit()
        return {"message": "Subscription paused", "pause_at": data.pause_at}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing subscription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/resume",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def resume_subscription(current_user: UnifiedAuthModel = Depends(require_active_user())):
    try:
        subscription = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.user_id == current_user.id,
                UserSubscriptionModel.status == SubscriptionStatusEnum.paused,
            )
            .order_by(UserSubscriptionModel.created_at.desc())
            .first()
        )
        if not subscription:
            raise HTTPException(status_code=404, detail="No paused subscription found")

        provider = PaymentProviderFactory.get_provider(subscription.provider)
        provider.resume_subscription(subscription.provider_subscription_id)

        subscription.status = SubscriptionStatusEnum.active
        db.session.commit()

        return {"message": "Subscription resumed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming subscription: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Invoices
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/invoices",
    response_model=InvoiceListResponse,
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def fetch_invoices(current_user: UnifiedAuthModel = Depends(require_active_user())):
    try:
        subscriptions = (
            db.session.query(UserSubscriptionModel)
            .filter(UserSubscriptionModel.user_id == current_user.id)
            .all()
        )

        all_invoices = []
        for sub in subscriptions:
            try:
                provider = PaymentProviderFactory.get_provider(sub.provider)
                invoices = provider.get_subscription_invoices(sub.provider_subscription_id)
                for inv in invoices:
                    all_invoices.append(
                        InvoiceItemResponse(
                            id=inv.get("id"),
                            amount=float(inv.get("amount", 0)) / 100.0,
                            status=inv.get("status"),
                            date=inv.get("date"),
                            invoice_url=inv.get("short_url") or inv.get("invoice_url"),
                            description=inv.get("description"),
                        )
                    )
            except Exception as e:
                logger.warning(f"Could not fetch invoices for subscription {sub.id}: {e}")

        return InvoiceListResponse(invoices=all_invoices)

    except Exception as e:
        logger.error(f"Error fetching invoices: {e}")
        raise HTTPException(status_code=500, detail=str(e))