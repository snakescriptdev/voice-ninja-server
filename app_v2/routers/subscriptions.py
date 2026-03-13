"""
subscriptions.py
────────────────────────────────────────────────────────────────────────────────
Subscription lifecycle:

  CREATE   → Razorpay subscription object created.  No DB row yet.
  VERIFY   → Frontend calls after checkout completes.
               • Signature verified.
               • DB row created (new) OR updated in-place (plan-change).
               • status set to  authenticated  (NOT active).
               • Coins are NOT credited here.
  WEBHOOK  subscription.charged
               → status promoted to  active.
               → Coins credited for the cycle.
               → This is the canonical "money received" event.
  WEBHOOK  subscription.activated
               → status confirmed as  authenticated  (no promotion to active).

Why authenticated ≠ active:
  Razorpay's authenticated state means the mandate is confirmed but the
  first charge has not yet been captured.  We grant feature access for
  authenticated subscriptions (see feature_access.py) because checkout
  completion is a very strong signal, but we don't credit coins or call the
  subscription "active" until real money has moved.

Single-row-per-user invariant:
  There is exactly ONE UserSubscriptionModel row per user at all times.
  Plan changes update that row in-place (verify PATH B).

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
      • cancel_at_period_end must be False  (not already scheduled for cancel/update)
      • order by created_at desc as tiebreaker (should never be needed after fix)
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
        pending_sub = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.user_id == current_user.id,
                UserSubscriptionModel.pending_provider_subscription_id.isnot(None),
            )
            .first()
        )
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
         data.razorpay_subscription_id.  Updates that row in-place with
         status=authenticated.

    In BOTH paths:
      • status is set to  authenticated  (NOT active).
      • Coins are NOT credited here.
      • Active status and coin credit happen exclusively in the
        subscription.charged webhook handler (_sub_charged in webhooks.py).

    Idempotent: if provider_subscription_id already set on any row, return success.
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
        pending_sub = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.user_id == current_user.id,
                UserSubscriptionModel.pending_provider_subscription_id == data.razorpay_subscription_id,
            )
            .first()
        )

        is_plan_change = pending_sub is not None
        downgrade_summary: dict = {}

        if is_plan_change:
            # ── PATH B: Plan change — update existing row in-place ────────────
            logger.info(
                f"verify_subscription: plan-change path | "
                f"sub_id={pending_sub.id} | "
                f"old_plan={pending_sub.plan_id} → new_plan={plan.id}"
            )
            subscription = pending_sub
            old_plan_id = pending_sub.plan_id

            # Swap the provider subscription id to the new Razorpay one
            subscription.provider_subscription_id = data.razorpay_subscription_id
            subscription.pending_provider_subscription_id = None

            # Mark authenticated — NOT active.
            # subscription.charged webhook will promote to active and credit coins.
            subscription.plan_id = plan.id
            subscription.next_plan_id = None
            subscription.status = SubscriptionStatusEnum.authenticated
            subscription.cancel_at_period_end = False
            subscription.current_period_start = current_start
            subscription.current_period_end = current_end

            # ── Downgrade enforcement ─────────────────────────────────────────
            # Run enforcement immediately so users can't exploit the window.
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
        if subscription.status == SubscriptionStatusEnum.authenticated:
            raise HTTPException(status_code=400, detail="Subscription cannot be cancelled before billing starts")

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
    Schedule a plan change.

    Flow:
      1. Cancel the current Razorpay subscription at cycle end.
      2. Create a new Razorpay subscription on the new plan.
      3. Stamp pending_provider_subscription_id + next_plan_id on the EXISTING
         subscription row — do NOT create a new DB row here.
      4. verify() will complete the in-place update (status=authenticated) when
         checkout succeeds.
      5. subscription.charged webhook will promote to active + credit coins.

    This keeps exactly ONE UserSubscriptionModel row per user at all times.
    """
    try:
        subscription = _get_current_subscription(current_user.id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")
        if subscription.status == SubscriptionStatusEnum.authenticated:
            raise HTTPException(status_code=400, detail="Subscription cannot be updated before billing starts")
        if subscription.plan_id == data.plan_id:
            raise HTTPException(status_code=400, detail="You are already on this plan")

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

        provider = PaymentProviderFactory.get_provider(subscription.provider)

        old_plan = db.session.query(PlanModel).filter(PlanModel.id == subscription.plan_id).first()
        is_downgrade = (old_plan is not None) and (new_plan.price < old_plan.price)

        result = provider.update_subscription(
            subscription_id=subscription.provider_subscription_id,
            new_plan_id=provider_plan.provider_plan_id,
            billing_period=new_plan.billing_period,
            start_at=subscription.current_period_end,
        )

        cancel_response = result["cancelled_subscription"]
        new_rzp_subscription = result["new_subscription"]
        new_rzp_sub_id: str = new_rzp_subscription["id"]

        # ── Stamp the pending state onto the EXISTING row ─────────────────────
        # Do NOT create a new UserSubscriptionModel row.
        # verify() will complete the in-place update when checkout succeeds.
        if subscription.subscription_metadata is None:
            subscription.subscription_metadata = {}
        subscription.subscription_metadata["customer_id"] = cancel_response.get("customer_id", "")
        subscription.subscription_metadata["pending_razorpay_subscription_id"] = new_rzp_sub_id

        # Mark as pending-update — still active/authenticated for feature access
        subscription.cancel_at_period_end = True
        subscription.next_plan_id = new_plan.id
        subscription.pending_provider_subscription_id = new_rzp_sub_id

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
        if subscription.status == SubscriptionStatusEnum.authenticated:
            raise HTTPException(status_code=400, detail="Subscription cannot be paused before billing starts")

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