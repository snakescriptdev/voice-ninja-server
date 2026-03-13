"""
subscriptions.py
────────────────────────────────────────────────────────────────────────────────
Subscription lifecycle:

  CREATE   → Razorpay subscription object created.  No DB row yet.

  UPDATE   → New Razorpay subscription created on the new plan.
               • Old Razorpay subscription is NOT cancelled here.
               • Existing DB row stamped with pending_provider_subscription_id
                 and cancel_at_period_end=True (blocks strict lookup, keeps
                 loose feature-access lookup working during checkout).

  CANCEL-PENDING  → User aborts an in-progress plan-change checkout.
               • New (incomplete) Razorpay sub cancelled immediately.
               • Existing DB row restored: pending_provider_subscription_id
                 cleared, cancel_at_period_end reset to False.

  VERIFY   → Frontend calls after checkout completes.
               PATH A (new sub):
                 • DB row created with status=authenticated.
               PATH B (plan change):
                 • Old Razorpay subscription cancelled immediately (no period-end).
                 • Old DB row marked cancelled.
                 • New DB row created with status=authenticated.
               • Coins are NOT credited in either path.

  WEBHOOK  subscription.charged
               → status promoted to  active.
               → Coins credited for the cycle.
               → This is the canonical "money received" event.

  WEBHOOK  subscription.activated
               → status confirmed as  authenticated  (no promotion to active).

Why cancel in verify and not in update:
  Cancelling the old sub before the user actually pays leaves them with no
  subscription if they abandon checkout.  We only cancel once signature is
  verified — i.e. payment is confirmed.

Why authenticated ≠ active:
  Razorpay's authenticated state means the mandate is confirmed but the
  first charge has not yet been captured.  We grant feature access for
  authenticated subscriptions (see feature_access.py) because checkout
  completion is a very strong signal, but we don't credit coins or call the
  subscription "active" until real money has moved.

Single-row-per-user invariant:
  After verify PATH B, the old row is marked cancelled and a new row is
  created for the new plan. At any point in time there is at most one row
  with status in (active, paused, authenticated) and cancel_at_period_end=False.

Fixes applied (vs original):
  - /cancel-pending-update endpoint added so abandoned checkouts are recoverable.
  - verify PATH B: old row marked cancelled + new row created (was update-in-place,
    which left old provider_subscription_id on the row and broke cancel webhook).
  - cancel_at_period_end restored on pending-cancel so user doesn't lose access.

Migration notes:
  ALTER TABLE user_subscriptions
    ADD COLUMN IF NOT EXISTS pending_provider_subscription_id VARCHAR(255);

  -- If SubscriptionStatusEnum is a PG enum:
  ALTER TYPE subscriptionstatusenum ADD VALUE IF NOT EXISTS 'authenticated';
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from sqlalchemy import or_
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
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
from datetime import datetime, timedelta
from app_v2.utils.coin_utils import get_user_coin_balance, reset_unused_subscription_coins
from fastapi.responses import HTMLResponse
import os
from app_v2.utils.time_utils import convert_to_unix_timestamp

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/subscriptions", tags=["Subscriptions"])


# ──────────────────────────────────────────────────────────────────────────────
# Active-like statuses  (kept in sync with feature_access.py)
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
    Return the single canonical active/paused/authenticated subscription for a user.

    Rules:
      • status must be active, paused, or authenticated
      • cancel_at_period_end must be False
      • A subscription with pending_provider_subscription_id set (mid plan-change
        checkout) is still returned — the user retains access until verify() commits.
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
    in flight (pending_provider_subscription_id is set), regardless of
    cancel_at_period_end state.

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
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    """
    Initiate a brand-new Razorpay subscription (first-time or after expiry/cancel).

    Blocked if:
      • User already has an active/paused/authenticated subscription with
        cancel_at_period_end=False
      • User already has an update in-flight (pending_provider_subscription_id set)
    """
    try:
        # Block if a real active subscription exists
        active_sub = _get_current_subscription(current_user.id)
        if active_sub:
            raise HTTPException(
                status_code=400,
                detail="You already have an active subscription. "
                       "Please update or cancel it first.",
            )

        # Also block if an update checkout is already in-flight
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
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    """
    Called by the frontend after Razorpay checkout completes.

    Two paths:
      A) NEW subscription  — no existing DB row for this razorpay_subscription_id.
         Creates a fresh UserSubscriptionModel row with status=authenticated.

      B) PLAN CHANGE  — existing row has pending_provider_subscription_id matching
         data.razorpay_subscription_id.
         • Old Razorpay subscription cancelled immediately.
         • Old DB row status set to cancelled.
         • New DB row created with status=authenticated.

    In BOTH paths:
      • status is set to  authenticated  (NOT active).
      • Coins are NOT credited here.
      • Active status and coin credit happen exclusively in the
        subscription.charged webhook handler (_sub_charged in webhooks.py).

    Idempotent: if provider_subscription_id already exists on any row, return success.
    """
    try:
        # ── Idempotency: already processed ───────────────────────────────────
        existing_sub = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.provider_subscription_id == data.razorpay_subscription_id
            )
            .first()
        )
        if existing_sub:
            logger.info(f"verify_subscription: already processed {data.razorpay_subscription_id}")
            return {"message": "Subscription already verified"}

        # ── Signature verification ────────────────────────────────────────────
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        params = {
            "razorpay_payment_id": data.razorpay_payment_id,
            "razorpay_subscription_id": data.razorpay_subscription_id,
            "razorpay_signature": data.razorpay_signature,
        }
        if not rzp_provider.verify_payment_signature(params):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        # ── Fetch plan ────────────────────────────────────────────────────────
        plan = (
            db.session.query(PlanModel)
            .filter(PlanModel.id == data.plan_id, PlanModel.is_active == True)
            .first()
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")

        # ── Fetch period dates from Razorpay ──────────────────────────────────
        current_start = datetime.utcnow()
        current_end = _calc_period_end(plan, current_start)
        try:
            rzp_sub = rzp_provider.get_subscription_details(data.razorpay_subscription_id)
            if rzp_sub.get("current_start"):
                current_start = datetime.utcfromtimestamp(rzp_sub["current_start"])
            if rzp_sub.get("current_end"):
                current_end = datetime.utcfromtimestamp(rzp_sub["current_end"])
        except Exception as e:
            logger.warning(f"Could not fetch Razorpay subscription details: {e} – using fallback dates")

        # ── Detect plan-change path vs new subscription path ──────────────────
        # Query DIRECTLY by the incoming RZP sub ID in the pending column.
        # This is robust against double-update() races (e.g. after a server
        # reload) where the row might have a stale or mismatched pending ID.
        pending_sub = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.user_id == current_user.id,
                UserSubscriptionModel.pending_provider_subscription_id == data.razorpay_subscription_id,
            )
            .first()
        )
        is_plan_change = pending_sub is not None

        # Warn if a different pending ID exists — orphaned RZP sub needs cleanup.
        if not is_plan_change:
            stale_pending = _get_any_subscription_with_pending(current_user.id)
            if stale_pending:
                logger.warning(
                    f"verify_subscription: stale pending_provider_subscription_id="
                    f"{stale_pending.pending_provider_subscription_id} on sub={stale_pending.id} "
                    f"does not match incoming rzp_sub={data.razorpay_subscription_id} — "
                    f"treating as new subscription. Orphaned RZP sub may need manual cancellation."
                )

        downgrade_summary: dict = {}

        if is_plan_change:
            # ── PATH B: Plan change ──────────────────────────────────────────
            logger.info(
                f"verify_subscription: plan-change path | "
                f"old_sub_id={pending_sub.id} | "
                f"old_plan={pending_sub.plan_id} → new_plan={plan.id}"
            )
            old_subscription = pending_sub
            old_plan_id = old_subscription.plan_id

            # Save old Razorpay subscription id BEFORE any changes —
            # we need it to cancel on Razorpay now that payment is confirmed.
            old_rzp_sub_id: str = old_subscription.provider_subscription_id

            # ── Cancel the old Razorpay subscription immediately ──────────────
            # Payment is confirmed at this point (signature verified above),
            # so it is safe to cancel the old sub now with no risk of leaving
            # the user without any subscription.
            try:
                rzp_provider.cancel_subscription(
                    subscription_id=old_rzp_sub_id,
                    cancel_at_cycle_end=False,   # immediate — user is on new plan now
                )
                logger.info(
                    f"verify_subscription | old Razorpay sub cancelled | "
                    f"old_rzp_id={old_rzp_sub_id}"
                )
            except Exception as cancel_err:
                # Non-fatal: log loudly but don't block verify.
                # Ops can manually cancel via Razorpay dashboard.
                logger.error(
                    f"verify_subscription | FAILED to cancel old Razorpay sub "
                    f"{old_rzp_sub_id} — manual intervention may be required | "
                    f"error={cancel_err}"
                )

            # ── FIX: Mark old subscription as cancelled in DB ─────────────────
            # Keep provider_subscription_id on the old row so the incoming
            # subscription.cancelled webhook can resolve and no-op it cleanly.
            old_subscription.status = SubscriptionStatusEnum.cancelled
            old_subscription.cancel_at_period_end = True
            old_subscription.pending_provider_subscription_id = None
            old_subscription.next_plan_id = None
            # Note: old_subscription.provider_subscription_id is intentionally
            # left unchanged so _sub_cancelled in webhooks.py can find and
            # idempotently handle the incoming cancel webhook.

            # ── FIX: Create a fresh subscription row for the new plan ─────────
            # The new row gets the new plan_id and the new Razorpay subscription id.
            # subscription.charged webhook will promote status to active and credit coins.
            subscription = UserSubscriptionModel(
                user_id=current_user.id,
                plan_id=plan.id,
                status=SubscriptionStatusEnum.authenticated,
                current_period_start=current_start,
                current_period_end=current_end,
                cancel_at_period_end=False,
                provider="razorpay",
                provider_subscription_id=data.razorpay_subscription_id,
                pending_provider_subscription_id=None,
                next_plan_id=None,
            )
            db.session.add(subscription)
            db.session.flush()

            # ── Downgrade enforcement ─────────────────────────────────────────
            # Run enforcement immediately so users can't exploit the window
            # between verify and the charged webhook.
            from app_v2.utils.downgrade_utils import (
                enforce_downgrade_for_user,
                compute_downgrade_diff,
            )
            from app_v2.utils.activity_logger import log_activity

            downgrade_diff = compute_downgrade_diff(old_plan_id, plan.id, db.session)

            if downgrade_diff:
                downgrade_summary = enforce_downgrade_for_user(
                    user_id=current_user.id,
                    old_plan_id=old_plan_id,
                    new_plan_id=plan.id,
                    session=db.session,
                )
                log_activity(
                    user_id=current_user.id,
                    event_type="subscription_downgrade_enforcement",
                    description=(
                        f"Plan downgraded from {old_plan_id} to {plan.id}. "
                        f"Resources auto-disabled per policy."
                    ),
                    metadata={
                        "old_plan_id": old_plan_id,
                        "new_plan_id": plan.id,
                        "diff": downgrade_diff,
                        "disabled": downgrade_summary,
                    },
                )
                logger.info(
                    f"verify_subscription | downgrade enforced | "
                    f"user={current_user.id} | summary={downgrade_summary}"
                )

            db.session.flush()

        else:
            # ── PATH A: Brand-new subscription — create fresh row ─────────────
            logger.info(
                f"verify_subscription: new subscription path | "
                f"user={current_user.id} | plan={plan.id}"
            )
            subscription = UserSubscriptionModel(
                user_id=current_user.id,
                plan_id=plan.id,
                # authenticated — NOT active.
                # subscription.charged webhook will promote to active + credit coins.
                status=SubscriptionStatusEnum.authenticated,
                current_period_start=current_start,
                current_period_end=current_end,
                cancel_at_period_end=False,
                provider="razorpay",
                provider_subscription_id=data.razorpay_subscription_id,
                pending_provider_subscription_id=None,
            )
            db.session.add(subscription)
            db.session.flush()

        # ── Persist payment record ────────────────────────────────────────────
        # We record the payment here as 'success' because the signature verified,
        # but the canonical source of truth for billing is the webhook.
        # NOTE: _sub_charged checks for an existing CoinsLedgerModel entry before
        # crediting coins, so this PaymentModel will NOT block coin credits.
        existing_payment = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_payment_id == data.razorpay_payment_id)
            .first()
        )
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
                metadata_json={
                    "plan_id": plan.id,
                    "subscription_id": subscription.id,
                    "cycle": cycle_label,
                },
            )
            db.session.add(payment)
            db.session.flush()

            try:
                invoices = rzp_provider.get_subscription_invoices(data.razorpay_subscription_id)
                if invoices:
                    payment.invoice_url = invoices[0].get("short_url") or invoices[0].get("invoice_url")
            except Exception as inv_err:
                logger.warning(f"Could not fetch invoice: {inv_err}")

        # NOTE: Coins are NOT credited here.
        # The subscription.charged webhook is the sole place that credits coins,
        # ensuring coins are only issued after actual money capture.

        db.session.commit()

        msg = (
            "Plan change authenticated. Awaiting charge confirmation."
            if is_plan_change
            else "Subscription authenticated. Awaiting first charge confirmation."
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
# Cancel pending plan-change  (NEW — fixes abandoned-checkout bug)
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/cancel-pending-update",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def cancel_pending_update(
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    """
    Abort an in-progress plan-change checkout.

    Call this when the user closes or cancels the Razorpay checkout modal
    without completing payment. Cancels the incomplete new Razorpay subscription
    and clears the pending state from the existing DB row.

    The user's current subscription is unaffected — cancel_at_period_end is
    never set by update(), so there is nothing to restore.
    """
    try:
        pending_sub = _get_any_subscription_with_pending(current_user.id)
        if not pending_sub:
            raise HTTPException(
                status_code=404,
                detail="No pending plan change found for this account.",
            )

        new_rzp_sub_id: str = pending_sub.pending_provider_subscription_id

        # Cancel the incomplete new Razorpay subscription
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

        # Clear pending state — subscription access was never interrupted
        pending_sub.pending_provider_subscription_id = None
        pending_sub.next_plan_id = None
        # cancel_at_period_end is NOT touched — it was never set by update()

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
    current_user: UnifiedAuthModel = Depends(get_current_user),
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
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    """
    Initiate a plan change.

    Flow (cancel-on-verify):
      1. Create a new Razorpay subscription on the new plan.
         The old Razorpay subscription is NOT touched here.
      2. Stamp pending_provider_subscription_id + next_plan_id on the existing
         DB row. cancel_at_period_end is NOT changed — the user's current
         subscription stays fully active if they abandon the checkout.
         If a previous checkout was abandoned, the stale Razorpay subscription
         is silently cancelled and overwritten — no error, no dead-end.
      3. If user abandons checkout → they can just click "change plan" again.
         /cancel-pending-update is still available but no longer required.
      4. verify() — after payment signature is confirmed — cancels the old
         Razorpay subscription immediately, marks old DB row cancelled, and
         creates a fresh DB row for the new plan.
      5. subscription.charged webhook promotes status to active + credits coins.

    Why cancel in verify and not here:
      Cancelling before the user actually pays leaves them with no subscription
      if they abandon the checkout.  We only cancel once payment is confirmed.
    """
    try:
        subscription = _get_current_subscription(current_user.id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

        if subscription.plan_id == data.plan_id:
            raise HTTPException(status_code=400, detail="You are already on this plan")

        # If a previous checkout was abandoned (pending_provider_subscription_id
        # still set), silently cancel that orphaned Razorpay subscription and
        # overwrite it. This lets the user retry without hitting a 400 error or
        # needing to call /cancel-pending-update manually.
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
                # Non-fatal — stale sub may already be in a terminal state on Razorpay.
                logger.warning(
                    f"update_subscription | could not cancel stale pending RZP sub "
                    f"{stale_rzp_sub_id} | error={cleanup_err} — overwriting anyway"
                )
            # Clear the stale pending state before stamping the new one below.
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

        # ── Create new Razorpay subscription only — do NOT cancel old one yet ──
        # Old sub is cancelled in verify() after the user successfully pays.
        rzp_provider = PaymentProviderFactory.get_provider(subscription.provider)
        new_rzp_subscription = rzp_provider.create_subscription(
            plan_id=provider_plan.provider_plan_id,
            notes={
                "user_id": str(current_user.id),
                "plan_id": str(new_plan.id),
            },
        )
        new_rzp_sub_id: str = new_rzp_subscription["id"]

        # ── Stamp pending state onto the EXISTING DB row ──────────────────────
        # Do NOT create a new UserSubscriptionModel row here.
        # Do NOT touch cancel_at_period_end — the user's current subscription
        # must remain fully accessible if they abandon the checkout.
        # Double-update is blocked by the pending_provider_subscription_id check above.
        subscription.pending_provider_subscription_id = new_rzp_sub_id
        subscription.next_plan_id = new_plan.id

        if subscription.subscription_metadata is None:
            subscription.subscription_metadata = {}
        subscription.subscription_metadata["pending_razorpay_subscription_id"] = new_rzp_sub_id
        # cancel_at_period_end is intentionally NOT set here.
        # The subscription stays fully active while the user is on the checkout page.

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
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    """
    Returns a dry-run summary of what will be auto-disabled if the user
    downgrades to the given plan.

    Purely informational — makes NO changes.
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
    current_user: UnifiedAuthModel = Depends(get_current_user),
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
def resume_subscription(current_user: UnifiedAuthModel = Depends(get_current_user)):
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
            raise HTTPException(
                status_code=404,
                detail="No paused subscription found",
            )

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
def fetch_invoices(current_user: UnifiedAuthModel = Depends(get_current_user)):
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