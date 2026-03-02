from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import UnifiedAuthModel, CoinPackageModel, PaymentModel, CoinsLedgerModel, AddOnCoinOrderModel
from app_v2.schemas.coin_purchase import OrderCreateRequest, OrderCreateResponse, OrderVerifyRequest
from app_v2.schemas.enum_types import PaymentProviderEnum, PaymentStatusEnum, PaymentTypeEnum, CoinTransactionTypeEnum
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timedelta
from app_v2.utils.coin_utils import get_user_coin_balance
from app_v2.schemas.admin_settings import CoinUsageSettingsResponse, CoinUsageSettingsUpdate
from app_v2.databases.models import CoinUsageSettingsModel
from fastapi.responses import HTMLResponse
import os

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/coins", tags=["Coins"])

@router.get("/checkout/demo", response_class=HTMLResponse)
async def get_addon_purchase_demo():
    """
    Serves the demo template for add-on coin purchase testing.
    """
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_addon_purchase.html")
    with open(template_path, "r") as f:
        return f.read()

@router.post("/checkout/create-order", response_model=OrderCreateResponse, dependencies=[Depends(security)], openapi_extra={"security":[{"BearerAuth":[]}]})
def create_coin_order(data: OrderCreateRequest, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        # 1. Get bundle details
        bundle = db.session.query(CoinPackageModel).filter(CoinPackageModel.id == data.bundle_id, CoinPackageModel.is_active == True).first()
        if not bundle:
            raise HTTPException(status_code=404, detail="Coin bundle not found or inactive")

        # 2. Create order in Razorpay
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        order = rzp_provider.create_order(
            amount=bundle.price,
            currency=bundle.currency,
            receipt=f"recp_addon_{current_user.id}_{datetime.utcnow().timestamp()}",
            notes={
                "user_id": str(current_user.id),
                "bundle_id": str(bundle.id),
                "type": "addon_purchase"
            }
        )

        # 3. Create AddOnCoinOrder record
        addon_order = AddOnCoinOrderModel(
            user_id=current_user.id,
            bundle_id=bundle.id,
            provider=PaymentProviderEnum.razorpay,
            provider_order_id=order["id"],
            amount=bundle.price,
            coins=bundle.coins,
            status=PaymentStatusEnum.pending
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
            bundle_name=bundle.name
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating coin order: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verify-payment", dependencies=[Depends(security)], openapi_extra={"security":[{"BearerAuth":[]}]})
def verify_coin_payment(data: OrderVerifyRequest, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")

        params = {
            "razorpay_order_id": data.razorpay_order_id,
            "razorpay_payment_id": data.razorpay_payment_id,
            "razorpay_signature": data.razorpay_signature
        }

        # 1. Verify Signature
        if not rzp_provider.verify_order_signature(params):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        # 2. Get Bundle
        bundle = db.session.query(CoinPackageModel).filter(CoinPackageModel.id == data.bundle_id).first()
        if not bundle:
            raise HTTPException(status_code=404, detail="Bundle not found")

        # 3. Create Payment Record
        payment = PaymentModel(
            user_id=current_user.id,
            amount=bundle.price,
            currency=bundle.currency,
            status=PaymentStatusEnum.success,
            provider=PaymentProviderEnum.razorpay,
            provider_payment_id=data.razorpay_payment_id,
            provider_order_id=data.razorpay_order_id,
            payment_type=PaymentTypeEnum.coin_purchase,
            metadata_json={
                "bundle_id": bundle.id,
                "coins": bundle.coins
            }
        )
        db.session.add(payment)
        db.session.flush()

        # 3a. Synthesize Invoice Details for Frontend
        invoice_details = {
            "id": f"INV-COIN-{payment.id}",
            "entity": "invoice",
            "invoice_number": f"VN-{datetime.utcnow().strftime('%Y%m%d')}-{payment.id}",
            "customer_details": {
                "name": current_user.name or "Customer",
                "email": current_user.email,
                "contact": current_user.phone
            },
            "line_items": [
                {
                    "name": f"Coin Bundle: {bundle.name}",
                    "description": f"Credit of {bundle.coins} coins with {bundle.validity_days or 'unlimited'} days validity",
                    "amount": int(bundle.price * 100),
                    "unit_amount": int(bundle.price * 100),
                    "quantity": 1,
                    "currency": bundle.currency
                }
            ],
            "amount": int(bundle.price * 100),
            "currency": bundle.currency,
            "status": "paid",
            "issued_at": int(datetime.utcnow().timestamp()),
            "paid_at": int(datetime.utcnow().timestamp())
        }
        payment.metadata_json["invoice_details"] = invoice_details
        payment.invoice_url = f"/api/v2/user-dashboard/billing/invoice/{payment.id}/view"

        # 4. Credit Coins to Ledger
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
            balance_after=new_balance
        )
        db.session.add(ledger_entry)

        # 5. Update AddOnCoinOrder record
        addon_order = db.session.query(AddOnCoinOrderModel).filter(
            AddOnCoinOrderModel.provider_order_id == data.razorpay_order_id
        ).first()
        if addon_order:
            addon_order.status = PaymentStatusEnum.success
            addon_order.provider_payment_id = data.razorpay_payment_id
            addon_order.provider_signature = data.razorpay_signature
            addon_order.payment_id = payment.id

        db.session.commit()

        return {"status": "success", "message": "Coins credited successfully", "new_balance": new_balance}

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error verifying coin payment: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Settings API
@router.get("/settings/coin-usage", response_model=CoinUsageSettingsResponse)
def get_coin_usage_settings():
    """Fetch global coin usage settings"""
    try:
        settings = CoinUsageSettingsModel.get_settings()
        return settings
    except Exception as e:
        logger.error(f"Error in get_coin_usage_settings: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.put("/settings/coin-usage", response_model=CoinUsageSettingsResponse)
def update_coin_usage_settings(data: CoinUsageSettingsUpdate):
    """Update global coin usage settings"""
    try:
        settings = CoinUsageSettingsModel.get_settings()
        
        with db():
            # Refresh to ensure we have the latest and are in the session
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
        logger.error(f"Error in update_coin_usage_settings: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )