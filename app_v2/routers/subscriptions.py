from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import UnifiedAuthModel, PlanModel, UserSubscriptionModel, PaymentModel, PlanProviderModel
from app_v2.schemas.subscriptions import SubscriptionCreate, SubscriptionResponse, SubscriptionVerifyRequest
from app_v2.schemas.enum_types import PaymentProviderEnum, SubscriptionStatusEnum, PaymentStatusEnum, PaymentTypeEnum
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from datetime import datetime, timedelta

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/subscriptions", tags=["Subscriptions"])


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


@router.post("/verify",openapi_extra={"security":[{"BearerAuth":[]}]},dependencies=[Depends(security)])
def verify_subscription(data: SubscriptionVerifyRequest, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        # 1. Verify signature
        rzp_provider = PaymentProviderFactory.get_provider("razorpay")
        params = {
            "razorpay_payment_id": data.razorpay_payment_id,
            "razorpay_subscription_id": data.razorpay_subscription_id,
            "razorpay_signature": data.razorpay_signature
        }
        
        if not rzp_provider.verify_payment_signature(params):
            raise HTTPException(status_code=400, detail="Invalid signature")
        #db population logic is yet to be implemented 
    except Exception as e:
        logger.error(f"Error verifying subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
