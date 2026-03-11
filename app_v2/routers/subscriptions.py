"""
subscriptions.py  (fixed – sync issues resolved)
────────────────────────────────────────────────────────────────────────────────
Key fixes vs previous version:

  PROBLEM 1 — update_subscription created a NEW UserSubscriptionModel row in
  verify(), leaving two `status=active` rows in the DB.  The webhook then
  looked up the subscription by provider_subscription_id and found the NEW row
  (sub_B), which had next_plan_id=None, so the plan-swap + enforcement never
  fired.  sub_A was left dangling as active forever.

  FIX — update_subscription() now immediately stamps the existing subscription
  row (sub_A) with:
    • pending_provider_subscription_id  = new Razorpay subscription id
    • next_plan_id                       = new plan id
    • cancel_at_period_end               = True
    • status                             = active  (unchanged, still the live sub)

  verify() detects it is completing a plan-change (pending_provider_subscription_id
  matches) and updates sub_A in-place instead of creating sub_B:
    • provider_subscription_id  ← new Razorpay id
    • pending_provider_subscription_id ← None  (cleared)
    • plan_id                   ← next_plan_id   (swapped NOW in verify)
    • next_plan_id              ← None           (cleared)
    • cancel_at_period_end      ← False          (fresh cycle)
    • current_period_start/end  ← from Razorpay

  The webhook subscription.charged now always finds ONE row by
  provider_subscription_id and next_plan_id is already cleared, so the old
  plan-swap logic is no longer needed there (but kept as a safety net).

  PROBLEM 2 — create_subscription guard allowed a second subscribe while an
  update was in flight (sub_A had cancel_at_period_end=True but status=active).

  FIX — guard now also blocks when pending_provider_subscription_id is set.

  PROBLEM 3 — cancel_subscription and pause_subscription used .first() without
  cancel_at_period_end filter, which could pick up the wrong row when two
  active rows existed.

  FIX — all single-subscription fetches now filter cancel_at_period_end=False
  to target only the "real" current subscription.

  PROBLEM 4 — resume_subscription had no status filter, could pick any row.

  FIX — resume now explicitly filters for paused status.

  PROBLEM 5 — models.py has no pending_provider_subscription_id column.

  FIX — add it to UserSubscriptionModel migration note below.  The column is
  nullable String(255).  Add this migration:
      ALTER TABLE user_subscriptions
        ADD COLUMN pending_provider_subscription_id VARCHAR(255);
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

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/subscriptions", tags=["Subscriptions"])


@router.get("/demo", response_class=HTMLResponse)
async def get_subscription_demo():
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_subscription.html")
    with open(template_path, "r") as f:
        return f.read()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers  (defined early so all endpoints can use them)
# ──────────────────────────────────────────────────────────────────────────────

def _get_current_subscription(user_id: int) -> "UserSubscriptionModel | None":
    """
    Return the single canonical active subscription for a user.

    Rules:
      • status must be active or paused
      • cancel_at_period_end must be False  (not already scheduled for cancel/update)
      • order by created_at desc as tiebreaker (should never be needed after fix)
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.user_id == user_id,
            or_(
                UserSubscriptionModel.status == SubscriptionStatusEnum.active,
                UserSubscriptionModel.status == SubscriptionStatusEnum.paused,
            ),
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
      • User already has an active subscription with cancel_at_period_end=False
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
         Creates a fresh UserSubscriptionModel row.

      B) PLAN CHANGE  — existing row has pending_provider_subscription_id matching
         data.razorpay_subscription_id.  Updates that row in-place.
         This keeps exactly ONE row per user and ensures the webhook
         subscription.charged lookup by provider_subscription_id always finds
         the correct single row.

    Idempotent: if provider_subscription_id already set on any row, return success.
    """
    try:
        # ── Idempotency: already fully activated ──────────────────────────────
        existing_sub = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.provider_subscription_id == data.razorpay_subscription_id
            )
            .first()
        )
        if existing_sub:
            logger.info(f"verify_subscription: already processed {data.razorpay_subscription_id}")
            return {"message": "Subscription already verified and active"}

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

        if is_plan_change:
            # ── PATH B: Plan change — update existing row in-place ────────────
            logger.info(
                f"verify_subscription: plan-change path | "
                f"sub_id={pending_sub.id} | "
                f"old_plan={pending_sub.plan_id} → new_plan={plan.id}"
            )
            subscription = pending_sub

            # Swap the provider subscription id to the new Razorpay one
            subscription.provider_subscription_id = data.razorpay_subscription_id
            subscription.pending_provider_subscription_id = None

            # Activate on new plan
            subscription.plan_id = plan.id
            subscription.next_plan_id = None
            subscription.status = SubscriptionStatusEnum.active
            subscription.cancel_at_period_end = False
            subscription.current_period_start = current_start
            subscription.current_period_end = current_end

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
                status=SubscriptionStatusEnum.active,
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

        # ── Credit coins ──────────────────────────────────────────────────────
        # Guard: webhook may have already credited for this subscription id
        already_credited = (
            db.session.query(CoinsLedgerModel)
            .filter(
                CoinsLedgerModel.reference_type == "subscription",
                CoinsLedgerModel.reference_id == subscription.id,
                CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.credit_subscription,
            )
            .first()
        )
        if not already_credited:
            if not plan.carry_forward_coins:
                reset_unused_subscription_coins(current_user.id)

            current_balance = get_user_coin_balance(current_user.id)
            new_balance = current_balance + plan.coins_included

            ledger_entry = CoinsLedgerModel(
                user_id=current_user.id,
                transaction_type=CoinTransactionTypeEnum.credit_subscription,
                coins=plan.coins_included,
                remaining_coins=plan.coins_included,
                expiry_at=current_end,
                reference_type="subscription",
                reference_id=subscription.id,
                balance_after=new_balance,
            )
            db.session.add(ledger_entry)

        db.session.commit()

        msg = "Plan changed successfully" if is_plan_change else "Subscription activated successfully"
        return {"message": msg}

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
      4. verify() will complete the in-place update when checkout succeeds.

    This keeps exactly ONE UserSubscriptionModel row per user at all times.
    """
    try:
        subscription = _get_current_subscription(current_user.id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

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
        result = provider.update_subscription(
            subscription_id=subscription.provider_subscription_id,
            new_plan_id=provider_plan.provider_plan_id,
            billing_period=new_plan.billing_period,
        )

        cancel_response = result["cancelled_subscription"]
        new_rzp_subscription = result["new_subscription"]
        new_rzp_sub_id: str = new_rzp_subscription["id"]

        # ── Stamp the pending state onto the EXISTING row ─────────────────────
        # Do NOT create a new UserSubscriptionModel row.
        # verify() will promote pending_provider_subscription_id →
        # provider_subscription_id when checkout completes.
        if subscription.subscription_metadata is None:
            subscription.subscription_metadata = {}
        subscription.subscription_metadata["customer_id"] = cancel_response.get("customer_id", "")
        subscription.subscription_metadata["pending_razorpay_subscription_id"] = new_rzp_sub_id

        # Mark as pending-update — still active for feature access until verify()
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
        }

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating subscription: {e}")
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
        # Only pause the canonical active subscription (not one pending cancel/update)
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
        # Explicitly look for a paused subscription only
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