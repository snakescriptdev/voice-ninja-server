"""
coin_purchase.py  (updated – production-grade)
────────────────────────────────────────────────────────────────────────────────
Key changes vs original:
  • verify_coin_payment is now idempotent – if the webhook (payment.captured)
    arrives before the frontend calls /verify, we detect the already-fulfilled
    order and return success without double-crediting.
  • Pending order is created in create_order; actual coin credit ONLY happens
    after signature verification succeeds in verify_coin_payment.
  • Failed payment path: if order is already marked failed we 409 rather than
    re-verifying.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer,is_admin
from app_v2.databases.models import (
    UnifiedAuthModel, CoinPackageModel, PaymentModel,
    CoinsLedgerModel, AddOnCoinOrderModel, CoinUsageSettingsModel,
)
from app_v2.schemas.coin_purchase import OrderCreateRequest, OrderCreateResponse, OrderVerifyRequest
from app_v2.schemas.enum_types import (
    PaymentProviderEnum, PaymentStatusEnum,
    PaymentTypeEnum, CoinTransactionTypeEnum,
)
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timedelta
from app_v2.utils.coin_utils import get_user_coin_balance
from app_v2.schemas.admin_settings import CoinUsageSettingsResponse, CoinUsageSettingsUpdate
from fastapi.responses import HTMLResponse
import os

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/coins", tags=["Coins"])


@router.get("/checkout/demo", response_class=HTMLResponse)
async def get_addon_purchase_demo():
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_addon_purchase.html")
    with open(template_path, "r") as f:
        return f.read()


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
    Create a Razorpay order for an add-on coin bundle and persist a pending
    AddOnCoinOrderModel.  The frontend uses the returned order_id to open the
    Razorpay checkout modal.
    """
    try:
        bundle = (
            db.session.query(CoinPackageModel)
            .filter(CoinPackageModel.id == data.bundle_id, CoinPackageModel.is_active == True)
            .first()
        )
        if not bundle:
            raise HTTPException(status_code=404, detail="Coin bundle not found or inactive")

        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        order = rzp_provider.create_order(
            amount=bundle.price,
            currency=bundle.currency,
            receipt=f"recp_addon_{current_user.id}_{int(datetime.utcnow().timestamp())}",
            notes={
                "user_id": str(current_user.id),
                "bundle_id": str(bundle.id),
                "type": "addon_purchase",
            },
        )

        addon_order = AddOnCoinOrderModel(
            user_id=current_user.id,
            bundle_id=bundle.id,
            provider=PaymentProviderEnum.razorpay,
            provider_order_id=order["id"],
            amount=bundle.price,
            coins=bundle.coins,
            status=PaymentStatusEnum.pending,
        )
        db.session.add(addon_order)
        db.session.commit()

        return OrderCreateResponse(
            order_id=order["id"],
            amount=bundle.price,
            currency=bundle.currency,
            key_id=VoiceSettings.RAZOR_KEY_ID,
            user_email=current_user.email or "",
            user_phone=current_user.phone or "",
            bundle_name=bundle.name,
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

        # ── Fetch bundle ──────────────────────────────────────────────────────
        bundle = (
            db.session.query(CoinPackageModel)
            .filter(CoinPackageModel.id == data.bundle_id)
            .first()
        )
        if not bundle:
            raise HTTPException(status_code=404, detail="Bundle not found")

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
            amount=bundle.price,
            currency=bundle.currency,
            status=PaymentStatusEnum.success,
            provider=PaymentProviderEnum.razorpay,
            provider_payment_id=data.razorpay_payment_id,
            provider_order_id=data.razorpay_order_id,
            payment_type=PaymentTypeEnum.coin_purchase,
            metadata_json={"bundle_id": bundle.id, "coins": bundle.coins},
        )
        db.session.add(payment)
        db.session.flush()

        # ── Credit coins ──────────────────────────────────────────────────────
        current_balance = get_user_coin_balance(current_user.id)
        new_balance = current_balance + bundle.coins

        expiry_date = None
        if bundle.validity_days is not None:
            expiry_date = datetime.utcnow() + timedelta(days=bundle.validity_days)

        ledger_entry = CoinsLedgerModel(
            user_id=current_user.id,
            transaction_type=CoinTransactionTypeEnum.credit_purchase,
            coins=bundle.coins,
            remaining_coins=bundle.coins,
            expiry_at=expiry_date,
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
            if data.phone_number_purchase_cost is not None:
                settings.phone_number_purchase_cost = data.phone_number_purchase_cost
            if data.elevenlabs_multiplier is not None:
                settings.elevenlabs_multiplier = data.elevenlabs_multiplier
            if data.static_conversation_cost is not None:
                settings.static_conversation_cost = data.static_conversation_cost
            db.session.commit()
            db.session.refresh(settings)
            return settings
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in update_coin_usage_settings: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))