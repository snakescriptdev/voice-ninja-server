"""
coin_purchase.py — pay-as-you-go credit purchase
────────────────────────────────────────────────────────────────────────────────
A user enters a rupee amount, gets a live credit estimate, and pays once via
Razorpay — no bundles, no stored card, no recurring mandate.

  • verify_coin_payment is idempotent – if the webhook (payment.captured)
    arrives before the frontend calls /verify, we detect the already-fulfilled
    order and return success without double-crediting.
  • Pending order is created in create_coin_order; actual coin credit ONLY
    happens after signature verification succeeds in verify_coin_payment.
  • Razorpay lets a user retry a failed payment attempt against the SAME
    order_id, so a prior payment.failed webhook marking addon_order as
    'failed' is not terminal — verify_coin_payment must still accept a
    later successful attempt on that order rather than reject it.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from sqlalchemy.exc import IntegrityError
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer, is_admin
from app_v2.databases.models import (
    UnifiedAuthModel, PaymentModel,
    CoinsLedgerModel, AddOnCoinOrderModel, CoinUsageSettingsModel,
)
from app_v2.schemas.coin_purchase import (
    OrderCreateRequest, OrderCreateResponse, OrderVerifyRequest,
    CreditEstimateResponse, PurchaseConfigResponse, CallConfigResponse,
    CreditBannerStatusResponse, DismissCreditBannerRequest,
    MIN_PURCHASE_AMOUNT,
)
from app_v2.schemas.enum_types import (
    PaymentProviderEnum, PaymentStatusEnum,
    PaymentTypeEnum, CoinTransactionTypeEnum,
)
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.utils.email_service import send_payment_success_email
from app_v2.utils.invoice_utils import generate_invoice_pdf, generate_invoice_reference
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timezone
from app_v2.utils.coin_utils import get_user_coin_balance, coins_to_inr, apply_banner_rearm
from app_v2.utils.conversation_lifecycle import SETTINGS_VERSION_FIELDS, maybe_create_new_settings_version
from app_v2.schemas.admin_settings import CoinUsageSettingsResponse, CoinUsageSettingsUpdate
from fastapi.responses import HTMLResponse
import os

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/coins", tags=["Coins"])


def _credits_for_amount(amount: float) -> int:
    settings = CoinUsageSettingsModel.get_settings()
    return round(amount * settings.credits_per_rupee)


@router.get("/checkout/demo", response_class=HTMLResponse)
async def get_addon_purchase_demo():
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_addon_purchase.html")
    with open(template_path, "r") as f:
        return f.read()


# ──────────────────────────────────────────────────────────────────────────────
# Live credit estimate (no DB write)
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/purchase-config",
    response_model=PurchaseConfigResponse,
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def get_purchase_config():
    """Public-to-users purchase config so the buy-credits UI can enforce the
    admin-configured minimum amount without hardcoding it."""
    settings = CoinUsageSettingsModel.get_settings()
    return PurchaseConfigResponse(
        minimum_purchase_amount_inr=settings.minimum_purchase_amount_inr,
        credits_per_rupee=settings.credits_per_rupee,
    )


@router.get(
    "/call-config",
    response_model=CallConfigResponse,
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def get_call_config():
    """Minimum-balance requirement for starting a call, so the dashboard can warn
    the user when their balance is too low to safely place/sustain a call."""
    s = CoinUsageSettingsModel.get_settings()
    return CallConfigResponse(
        minimum_call_balance=int(s.minimum_credits_per_minute * s.minimum_call_minutes),
        minimum_credits_per_minute=s.minimum_credits_per_minute,
        minimum_call_minutes=s.minimum_call_minutes,
        first_call_max_duration_seconds=s.first_call_max_duration_seconds,
    )


# The "low credits" header banner (mid-call-cutoff warning) fires this many
# times earlier than the "critical" (can't-start-a-call) banner.
LOW_CREDITS_MULTIPLIER = 2


@router.get(
    "/credit-banners",
    response_model=CreditBannerStatusResponse,
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def get_credit_banner_status(current_user: UnifiedAuthModel = Depends(require_active_user())):
    """
    Whether the two low-credit header banners should be shown for the current
    user right now. Persists the dismissal/re-arm state on the user row so a
    dismissal sticks across devices and sessions (see apply_banner_rearm).
    """
    settings = CoinUsageSettingsModel.get_settings()
    minimum_call_balance = int(settings.minimum_credits_per_minute * settings.minimum_call_minutes)
    low_credits_threshold = minimum_call_balance * LOW_CREDITS_MULTIPLIER
    available_coins = get_user_coin_balance(current_user.id)

    user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    low_dismissed, low_recovered, show_low = apply_banner_rearm(
        user.low_credits_banner_dismissed,
        user.low_credits_banner_recovered,
        available_coins < low_credits_threshold,
    )
    critical_dismissed, critical_recovered, show_critical = apply_banner_rearm(
        user.critical_credits_banner_dismissed,
        user.critical_credits_banner_recovered,
        available_coins < minimum_call_balance,
    )

    if (
        low_dismissed != user.low_credits_banner_dismissed
        or low_recovered != user.low_credits_banner_recovered
        or critical_dismissed != user.critical_credits_banner_dismissed
        or critical_recovered != user.critical_credits_banner_recovered
    ):
        user.low_credits_banner_dismissed = low_dismissed
        user.low_credits_banner_recovered = low_recovered
        user.critical_credits_banner_dismissed = critical_dismissed
        user.critical_credits_banner_recovered = critical_recovered
        db.session.commit()

    return CreditBannerStatusResponse(
        available_amount=coins_to_inr(available_coins, settings.credits_per_rupee),
        minimum_call_balance_amount=coins_to_inr(minimum_call_balance, settings.credits_per_rupee),
        show_low_credits_banner=show_low,
        show_critical_credits_banner=show_critical,
    )


@router.post(
    "/credit-banners/dismiss",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def dismiss_credit_banner(
    data: DismissCreditBannerRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Persist that the current user closed one of the low-credit header
    banners, so it stays hidden (until it re-arms — see apply_banner_rearm)."""
    user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if data.banner == "low_credits":
        user.low_credits_banner_dismissed = True
        user.low_credits_banner_recovered = False
    else:
        user.critical_credits_banner_dismissed = True
        user.critical_credits_banner_recovered = False
    db.session.commit()
    return {"message": "Banner dismissed"}


@router.get(
    "/estimate",
    response_model=CreditEstimateResponse,
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def estimate_credits(amount: float):
    minimum = CoinUsageSettingsModel.get_settings().minimum_purchase_amount_inr
    if amount < minimum:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum purchase amount is ₹{minimum:.0f}",
        )
    return CreditEstimateResponse(amount=amount, credits=_credits_for_amount(amount))


# ──────────────────────────────────────────────────────────────────────────────
# Create order
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/checkout/create-order",
    response_model=OrderCreateResponse,
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def create_coin_order(
    data: OrderCreateRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Create a Razorpay order for a pay-as-you-go credit purchase and persist a
    pending AddOnCoinOrderModel. The frontend uses the returned order_id to
    open the Razorpay checkout modal.
    """
    try:
        minimum = CoinUsageSettingsModel.get_settings().minimum_purchase_amount_inr
        if data.amount < minimum:
            raise HTTPException(
                status_code=400,
                detail=f"Minimum purchase amount is ₹{minimum:.0f}",
            )

        credits = _credits_for_amount(data.amount)

        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        order = rzp_provider.create_order(
            amount=data.amount,
            currency="INR",
            receipt=f"recp_addon_{current_user.id}_{int(datetime.now(timezone.utc).timestamp())}",
            notes={
                "user_id": str(current_user.id),
                "type": "addon_purchase",
            },
        )

        addon_order = AddOnCoinOrderModel(
            user_id=current_user.id,
            provider=PaymentProviderEnum.razorpay,
            provider_order_id=order["id"],
            amount=data.amount,
            coins=credits,
            status=PaymentStatusEnum.pending,
        )
        db.session.add(addon_order)
        db.session.commit()

        return OrderCreateResponse(
            order_id=order["id"],
            amount=data.amount,
            currency="INR",
            key_id=VoiceSettings.RAZOR_KEY_ID,
            user_email=current_user.email or "",
            user_phone=current_user.phone or "",
            credits=credits,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating coin order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Verify payment (frontend callback after checkout)
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/verify-payment",
    dependencies=[Depends(security)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def verify_coin_payment(
    data: OrderVerifyRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Called by the frontend after the user completes checkout.

    Idempotency contract:
      • If the webhook (payment.captured) already fulfilled this order the
        addon_order.status will be 'success' → return success without any DB
        writes.
      • addon_order.status of 'failed' is NOT terminal — Razorpay allows
        retrying a payment against the same order_id, so a prior failed
        attempt must not block crediting a later successful one.
      • Otherwise, verify signature, credit coins, record payment.
    """
    try:
        # ── Locate the pending order ──────────────────────────────────────────
        addon_order = (
            db.session.query(AddOnCoinOrderModel)
            .filter(AddOnCoinOrderModel.provider_order_id == data.razorpay_order_id)
            .first()
        )
        if not addon_order:
            raise HTTPException(status_code=404, detail="Order not found")

        if addon_order.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Order does not belong to you")

        # ── Idempotency: webhook may have already fulfilled it ────────────────
        if addon_order.status == PaymentStatusEnum.success:
            current_balance = get_user_coin_balance(current_user.id)
            return {
                "status": "success",
                "message": "Coins already credited",
                "new_balance": current_balance,
            }

        # ── Verify Razorpay signature ─────────────────────────────────────────
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        params = {
            "razorpay_order_id": data.razorpay_order_id,
            "razorpay_payment_id": data.razorpay_payment_id,
            "razorpay_signature": data.razorpay_signature,
        }
        if not rzp_provider.verify_order_signature(params):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        # ── Guard against duplicate payment_id (webhook race) ─────────────────
        existing_payment = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.provider_payment_id == data.razorpay_payment_id)
            .first()
        )
        if existing_payment:
            # Webhook already created the payment; just ensure order is marked
            addon_order.status = PaymentStatusEnum.success
            addon_order.provider_payment_id = data.razorpay_payment_id
            addon_order.provider_signature = data.razorpay_signature
            addon_order.payment_id = existing_payment.id
            db.session.commit()
            current_balance = get_user_coin_balance(current_user.id)
            return {
                "status": "success",
                "message": "Coins credited (webhook processed first)",
                "new_balance": current_balance,
            }

        # ── Record payment ────────────────────────────────────────────────────
        # Savepoint: the payment.captured webhook can be racing this exact
        # request. Whichever insert commits first wins the unique index on
        # provider_payment_id; the loser falls back to the winner's row below
        # instead of double-crediting the ledger.
        try:
            with db.session.begin_nested():
                payment = PaymentModel(
                    user_id=current_user.id,
                    amount=addon_order.amount,
                    currency="INR",
                    status=PaymentStatusEnum.success,
                    provider=PaymentProviderEnum.razorpay,
                    provider_payment_id=data.razorpay_payment_id or None,
                    provider_order_id=data.razorpay_order_id,
                    payment_type=PaymentTypeEnum.coin_purchase,
                    metadata_json={"coins": addon_order.coins},
                    invoice_reference=generate_invoice_reference(),
                )
                db.session.add(payment)
                db.session.flush()

                # ── Credit coins ────────────────────────────────────────────────
                current_balance = get_user_coin_balance(current_user.id)
                new_balance = current_balance + addon_order.coins

                ledger_entry = CoinsLedgerModel(
                    user_id=current_user.id,
                    transaction_type=CoinTransactionTypeEnum.credit_purchase,
                    coins=addon_order.coins,
                    remaining_coins=addon_order.coins,
                    reference_type="payment",
                    reference_id=payment.id,
                    balance_after=new_balance,
                )
                db.session.add(ledger_entry)

                # ── Finalise addon order ────────────────────────────────────────
                addon_order.status = PaymentStatusEnum.success
                addon_order.provider_payment_id = data.razorpay_payment_id
                addon_order.provider_signature = data.razorpay_signature
                addon_order.payment_id = payment.id
        except IntegrityError:
            # Webhook won the race — fall back to its row instead of double-crediting.
            existing_payment = (
                db.session.query(PaymentModel)
                .filter(PaymentModel.provider_payment_id == data.razorpay_payment_id)
                .first()
            )
            addon_order.status = PaymentStatusEnum.success
            addon_order.provider_payment_id = data.razorpay_payment_id
            addon_order.provider_signature = data.razorpay_signature
            addon_order.payment_id = existing_payment.id if existing_payment else None
            db.session.commit()
            current_balance = get_user_coin_balance(current_user.id)
            return {
                "status": "success",
                "message": "Coins credited (webhook processed first)",
                "new_balance": current_balance,
            }

        db.session.commit()

        if current_user.email:
            try:
                await send_payment_success_email(
                    user_email=current_user.email,
                    user_name=current_user.name,
                    amount=addon_order.amount,
                    currency="INR",
                    provider_payment_id=data.razorpay_payment_id,
                    base_url=VoiceSettings.FRONTEND_URL,
                    invoice_pdf=generate_invoice_pdf(payment, current_user),
                )
            except Exception:
                logger.exception("verify_coin_payment: failed to send payment success email")

        return {
            "status": "success",
            "message": "Coins credited successfully",
            "new_balance": new_balance,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error verifying coin payment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Coin usage settings
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/settings/coin-usage", response_model=CoinUsageSettingsResponse,dependencies=[Depends(is_admin)],openapi_extra={"security": [{"BearerAuth": []}]})
def get_coin_usage_settings():
    try:
        return CoinUsageSettingsModel.get_settings()
    except Exception as e:
        logger.error(f"Error in get_coin_usage_settings: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.put("/settings/coin-usage", response_model=CoinUsageSettingsResponse, openapi_extra={"security": [{"BearerAuth": []}]})
def update_coin_usage_settings(data: CoinUsageSettingsUpdate, admin: UnifiedAuthModel = Depends(is_admin)):
    try:
        settings = CoinUsageSettingsModel.get_settings()
        with db():
            db.session.add(settings)
            before = {field: getattr(settings, field) for field in SETTINGS_VERSION_FIELDS}
            admin_identity = admin.email or admin.username or f"user#{admin.id}"
            settings.updated_by = admin_identity
            if settings.field_update_meta is None:
                settings.field_update_meta = {}
            field_update_stamp = {"updated_by": admin_identity, "updated_at": datetime.now(timezone.utc).isoformat()}
            if data.elevenlabs_conversation_credits_per_minute is not None:
                settings.elevenlabs_conversation_credits_per_minute = data.elevenlabs_conversation_credits_per_minute
                settings.field_update_meta["elevenlabs_conversation_credits_per_minute"] = field_update_stamp
            if data.usd_to_credits is not None:
                settings.usd_to_credits = data.usd_to_credits
                settings.field_update_meta["usd_to_credits"] = field_update_stamp
            if data.markup_percentage is not None:
                settings.markup_percentage = data.markup_percentage
            if data.minimum_credits_per_minute is not None:
                settings.minimum_credits_per_minute = data.minimum_credits_per_minute
            if data.minimum_call_minutes is not None:
                settings.minimum_call_minutes = data.minimum_call_minutes
            if data.first_call_max_duration_seconds is not None:
                settings.first_call_max_duration_seconds = data.first_call_max_duration_seconds
            if data.knowledge_base_llm_cost_multiplier is not None:
                settings.knowledge_base_llm_cost_multiplier = data.knowledge_base_llm_cost_multiplier
            if data.tool_llm_cost_multiplier is not None:
                settings.tool_llm_cost_multiplier = data.tool_llm_cost_multiplier
            if data.credits_per_rupee is not None:
                settings.credits_per_rupee = data.credits_per_rupee
            if data.minimum_purchase_amount_inr is not None:
                settings.minimum_purchase_amount_inr = data.minimum_purchase_amount_inr
            maybe_create_new_settings_version(settings, before)
            db.session.commit()
            db.session.refresh(settings)
            return settings
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in update_coin_usage_settings: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
