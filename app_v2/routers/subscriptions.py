from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import UnifiedAuthModel, PlanModel, UserSubscriptionModel, PaymentModel, PlanProviderModel, CoinsLedgerModel
from app_v2.schemas.subscriptions import SubscriptionCreate, SubscriptionResponse, SubscriptionVerifyRequest
from app_v2.schemas.enum_types import PaymentProviderEnum, SubscriptionStatusEnum, PaymentStatusEnum, PaymentTypeEnum, CoinTransactionTypeEnum  
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timedelta

from fastapi.responses import HTMLResponse
import os

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/subscriptions", tags=["Subscriptions"])

@router.get("/demo", response_class=HTMLResponse)
async def get_subscription_demo():
    """
    Serves the demo template for subscription testing.
    """
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_subscription.html")
    with open(template_path, "r") as f:
        return f.read()


@router.post("/create", response_model=SubscriptionResponse, dependencies=[Depends(security)],openapi_extra={"security":[{"BearerAuth":[]}]})
def create_subscription(data: SubscriptionCreate, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        # 1. Get plan details
        plan = db.session.query(PlanModel).filter(PlanModel.id == data.plan_id, PlanModel.is_active == True).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found or inactive")

        # 2. Get Razorpay plan ID from PlanProviderModel
        provider_plan = db.session.query(PlanProviderModel).filter(
            PlanProviderModel.plan_id == plan.id,
            PlanProviderModel.provider == PaymentProviderEnum.razorpay,
            PlanProviderModel.is_active == True
        ).first()
        
        if not provider_plan:
            raise HTTPException(status_code=400, detail="Razorpay plan not configured for this plan")

        # 3. Create subscription in Razorpay
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        subscription = rzp_provider.create_subscription(
            plan_id=provider_plan.provider_plan_id,
            notes={
                "user_id": str(current_user.id),
                "plan_id": str(plan.id)
            }
        )

        return SubscriptionResponse(
            subscription_id=subscription["id"],
            amount=plan.price,
            currency=plan.currency,
            plan_name=plan.display_name,
            user_email=current_user.email,
            user_phone=current_user.phone,
            key_id=VoiceSettings.RAZOR_KEY_ID
        )
    except Exception as e:
        logger.error(f"Error creating subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/verify",
    openapi_extra={"security": [{"BearerAuth": []}]},
    dependencies=[Depends(security)]
)
def verify_subscription(
    data: SubscriptionVerifyRequest,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")

        params = {
            "razorpay_payment_id": data.razorpay_payment_id,
            "razorpay_subscription_id": data.razorpay_subscription_id,
            "razorpay_signature": data.razorpay_signature
        }
        logger.info(f"Verifying subscription: {params}")
        logger.info(f"Razorpay key id: {VoiceSettings.RAZOR_KEY_ID}")
        logger.info(f"Razorpay key secret: {VoiceSettings.RAZOR_KEY_SECRET}")

        # 1️⃣ Verify Signature
        response = rzp_provider.verify_payment_signature(params)
        logger.info(f"Signature verification response: {response}")
        if not response:
            raise HTTPException(status_code=400, detail="Invalid signature")

        # 2️⃣ Fetch Plan
        plan = db.session.query(PlanModel).filter(
            PlanModel.id == data.plan_id,
            PlanModel.is_active == True
        ).first()

        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")

        # 3️⃣ Calculate billing period
        current_start = datetime.utcnow()

        if plan.billing_period.value == "monthly":
            current_end = current_start + timedelta(days=30)
        elif plan.billing_period.value == "yearly":
            current_end = current_start + timedelta(days=365)
        else:
            current_end = current_start + timedelta(days=30)

        # 4️⃣ Create Subscription Record
        subscription = UserSubscriptionModel(
            user_id=current_user.id,
            plan_id=plan.id,
            status=SubscriptionStatusEnum.active,
            current_period_start=current_start,
            current_period_end=current_end,
            cancel_at_period_end=False,
            provider="razorpay",
            provider_subscription_id=data.razorpay_subscription_id
        )

        db.session.add(subscription)
        db.session.flush()  # to get subscription.id

        # 5️⃣ Create Payment Record
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
                "subscription_id": subscription.id
            }
        )

        db.session.add(payment)

        # 6️⃣ Credit Coins
        # Get current balance
        last_ledger = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == current_user.id
        ).order_by(CoinsLedgerModel.id.desc()).first()

        current_balance = last_ledger.balance_after if last_ledger else 0
        new_balance = current_balance + plan.coins_included

        ledger_entry = CoinsLedgerModel(
            user_id=current_user.id,
            transaction_type=CoinTransactionTypeEnum.credit_subscription,
            coins=plan.coins_included,
            reference_type="subscription",
            reference_id=subscription.id,
            balance_after=new_balance
        )

        db.session.add(ledger_entry)

        # 7️⃣ Commit Everything
        db.session.commit()

        return {"message": "Subscription activated successfully"}

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error verifying subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))