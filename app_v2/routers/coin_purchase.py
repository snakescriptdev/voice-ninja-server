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
  • Failed payment path: if order is already marked failed we 409 rather than
    re-verifying.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer, is_admin
from app_v2.databases.models import (
    UnifiedAuthModel, PaymentModel,
    CoinsLedgerModel, AddOnCoinOrderModel, CoinUsageSettingsModel,
)
from app_v2.schemas.coin_purchase import (
    OrderCreateRequest, OrderCreateResponse, OrderVerifyRequest,
    CreditEstimateResponse, PurchaseConfigResponse, MIN_PURCHASE_AMOUNT,
)
from app_v2.schemas.enum_types import (
    PaymentProviderEnum, PaymentStatusEnum,
    PaymentTypeEnum, CoinTransactionTypeEnum,
)
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timezone
from app_v2.utils.coin_utils import get_user_coin_balance
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
def verify_coin_payment(
    data: OrderVerifyRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Called by the frontend after the user completes checkout.

    Idempotency contract:
      • If the webhook (payment.captured) already fulfilled this order the
        addon_order.status will be 'success' → return success without any DB
        writes.
      • If addon_order.status is 'failed' → 409 (user should retry with a new
        order).
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

        if addon_order.status == PaymentStatusEnum.failed:
            raise HTTPException(
                status_code=409,
                detail="This order was marked as failed. Please create a new order.",
            )

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
        payment = PaymentModel(
            user_id=current_user.id,
            amount=addon_order.amount,
            currency="INR",
            status=PaymentStatusEnum.success,
            provider=PaymentProviderEnum.razorpay,
            provider_payment_id=data.razorpay_payment_id,
            provider_order_id=data.razorpay_order_id,
            payment_type=PaymentTypeEnum.coin_purchase,
            metadata_json={"coins": addon_order.coins},
        )
        db.session.add(payment)
        db.session.flush()

        # ── Credit coins ──────────────────────────────────────────────────────
        current_balance = get_user_coin_balance(current_user.id)
        new_balance = current_balance + addon_order.coins

        ledger_entry = CoinsLedgerModel(
            user_id=current_user.id,
            transaction_type=CoinTransactionTypeEnum.credit_purchase,
            coins=addon_order.coins,
            remaining_coins=addon_order.coins,
            expiry_at=None,
            reference_type="payment",
            reference_id=payment.id,
            balance_after=new_balance,
        )
        db.session.add(ledger_entry)

        # ── Finalise addon order ──────────────────────────────────────────────
        addon_order.status = PaymentStatusEnum.success
        addon_order.provider_payment_id = data.razorpay_payment_id
        addon_order.provider_signature = data.razorpay_signature
        addon_order.payment_id = payment.id

        db.session.commit()

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


@router.put("/settings/coin-usage", response_model=CoinUsageSettingsResponse, dependencies=[Depends(is_admin)],openapi_extra={"security": [{"BearerAuth": []}]})
def update_coin_usage_settings(data: CoinUsageSettingsUpdate):
    try:
        settings = CoinUsageSettingsModel.get_settings()
        with db():
            db.session.add(settings)
            if data.elevenlabs_conversation_credits_per_minute is not None:
                settings.elevenlabs_conversation_credits_per_minute = data.elevenlabs_conversation_credits_per_minute
            if data.usd_to_credits is not None:
                settings.usd_to_credits = data.usd_to_credits
            if data.markup_percentage is not None:
                settings.markup_percentage = data.markup_percentage
            if data.minimum_credits_per_minute is not None:
                settings.minimum_credits_per_minute = data.minimum_credits_per_minute
            if data.minimum_call_minutes is not None:
                settings.minimum_call_minutes = data.minimum_call_minutes
            if data.credits_per_rupee is not None:
                settings.credits_per_rupee = data.credits_per_rupee
            if data.minimum_purchase_amount_inr is not None:
                settings.minimum_purchase_amount_inr = data.minimum_purchase_amount_inr
            db.session.commit()
            db.session.refresh(settings)
            return settings
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in update_coin_usage_settings: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
