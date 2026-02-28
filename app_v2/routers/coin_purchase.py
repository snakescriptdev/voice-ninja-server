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

from fastapi.responses import HTMLResponse
import os

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/coin-purchase", tags=["Coin Purchase"])

@router.get("/demo", response_class=HTMLResponse)
async def get_addon_purchase_demo():
    """
    Serves the demo template for add-on coin purchase testing.
    """
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_addon_purchase.html")
    with open(template_path, "r") as f:
        return f.read()

@router.post("/create-order", response_model=OrderCreateResponse, dependencies=[Depends(security)], openapi_extra={"security":[{"BearerAuth":[]}]})
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

        # 4. Credit Coins to Ledger
        current_balance = get_user_coin_balance(current_user.id)
        new_balance = current_balance + bundle.coins

        expiry_date = None
        if bundle.validity_days:
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
