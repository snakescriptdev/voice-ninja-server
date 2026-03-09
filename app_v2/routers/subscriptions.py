from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from sqlalchemy import or_
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import UnifiedAuthModel, PlanModel, UserSubscriptionModel, PaymentModel, PlanProviderModel, CoinsLedgerModel
from app_v2.schemas.subscriptions import (
    SubscriptionCreate, SubscriptionResponse, SubscriptionVerifyRequest, 
    SubscriptionCancelRequest, SubscriptionUpdateRequest, SubscriptionPauseRequest,
    InvoiceListResponse, InvoiceItemResponse
)
from app_v2.schemas.enum_types import PaymentProviderEnum, SubscriptionStatusEnum, PaymentStatusEnum, PaymentTypeEnum, CoinTransactionTypeEnum  
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
    """
    Serves the demo template for subscription testing.
    """
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "demo_subscription.html")
    with open(template_path, "r") as f:
        return f.read()


@router.post("/create", response_model=SubscriptionResponse, dependencies=[Depends(security)],openapi_extra={"security":[{"BearerAuth":[]}]})
def create_subscription(data: SubscriptionCreate, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        # 0. Check for existing active subscription
        active_sub = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == current_user.id,
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).first()
        if active_sub:
            raise HTTPException(
                status_code=400, 
                detail="You already have an active subscription. Please update your current subscription instead of creating a new one."
            )

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
            plan_id=plan.id,
            plan_name=plan.display_name,
            user_email=current_user.email,
            user_phone=current_user.phone,
            key_id=VoiceSettings.RAZOR_KEY_ID
        )
    except HTTPException:
        raise
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
        existing = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.provider_subscription_id == data.razorpay_subscription_id
        ).first()

        if existing:
            return {"message": "Subscription already verified"}
        
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

        # 3️⃣ Fetch Subscription Details from Razorpay and Calculate billing period
        try:
            rzp_subscription = rzp_provider.get_subscription_details(data.razorpay_subscription_id)
            current_start = datetime.fromtimestamp(rzp_subscription.get("current_start")) if rzp_subscription.get("current_start") else datetime.utcnow()
            current_end = datetime.fromtimestamp(rzp_subscription.get("current_end")) if rzp_subscription.get("current_end") else datetime.utcnow()
            
            # If rzp doesn't provide timestamps (unlikely for active sub), fallback to plan based calculation
            if not rzp_subscription.get("current_end"):
                if plan.billing_period.value == "monthly":
                    current_end = current_start + timedelta(days=30)
                elif plan.billing_period.value == "yearly":
                    current_end = current_start + timedelta(days=365)
                else:
                    current_end = current_start + timedelta(days=30)
        except Exception as e:
            logger.error(f"Failed to fetch subscription details from Razorpay: {str(e)}")
            # Fallback to manual calculation if fetching fails
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
        db.session.flush()

        # Try to fetch invoice details for the subscription
        try:
            invoices = rzp_provider.get_subscription_invoices(data.razorpay_subscription_id)
            if invoices:
                # Razorpay invoices are usually returned in descending order of creation
                # The first one should be the one just generated for this charge
                invoice = invoices[0]
                
                # Internal URL for viewing the invoice
                payment.invoice_url = invoice.get("short_url") or invoice.get("invoice_url")
                logger.info(f"Stored full invoice link for payment: {payment.id}")
        except Exception as inv_err:
            logger.error(f"Failed to fetch invoice for subscription {data.razorpay_subscription_id}: {inv_err}")
            # Don't fail the verification if only invoice fetching fails

        # 6️⃣ Credit Coins
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
            balance_after=new_balance
        )

        db.session.add(ledger_entry)

        # 7️⃣ Commit Everything
        db.session.commit()

        return {"message": "Subscription activated successfully"}
    except HTTPException as e:
        raise e
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error verifying subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cancel", dependencies=[Depends(security)], openapi_extra={"security": [{"BearerAuth": []}]})
def cancel_subscription(data: SubscriptionCancelRequest, current_user: UnifiedAuthModel = Depends(get_current_user)):
    """
    Cancel the user's active subscription.
    """
    try:
        subscription = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == current_user.id,
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).first()

        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

        provider = PaymentProviderFactory.get_provider(subscription.provider)
        response = provider.cancel_subscription(
            subscription.provider_subscription_id, 
            data.cancel_at_cycle_end
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
        return {"message": "Subscription cancellation initiated", "provider_response": response}
    except HTTPException as e:
        raise e
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error cancelling subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/update", dependencies=[Depends(security)], openapi_extra={"security": [{"BearerAuth": []}]})
def update_subscription(data: SubscriptionUpdateRequest, current_user: UnifiedAuthModel = Depends(get_current_user)):
    """
    Update the user's active subscription to a new plan.
    """
    try:
        subscription = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == current_user.id,
            or_(UserSubscriptionModel.status == SubscriptionStatusEnum.active,UserSubscriptionModel.status==SubscriptionStatusEnum.paused),
            UserSubscriptionModel.cancel_at_period_end == False
        ).first()

        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

        # Fetch new plan
        new_plan = db.session.query(PlanModel).filter(
            PlanModel.id == data.plan_id,
            PlanModel.is_active == True
        ).first()

        if not new_plan:
            raise HTTPException(status_code=404, detail="New plan not found or inactive")

        # Fetch provider plan mapping
        provider_plan = db.session.query(PlanProviderModel).filter(
            PlanProviderModel.plan_id == new_plan.id,
            PlanProviderModel.provider == subscription.provider,
            PlanProviderModel.is_active == True
        ).first()

        if not provider_plan:
            raise HTTPException(
                status_code=400,
                detail=f"Provider plan not configured for {subscription.provider}"
            )

        provider = PaymentProviderFactory.get_provider(subscription.provider)

        logger.info(
            f"Updating subscription {subscription.provider_subscription_id} "
            f"for user {current_user.id}"
        )

        # 🔹 Call your utility
        result = provider.update_subscription(
            subscription_id=subscription.provider_subscription_id,
            new_plan_id=provider_plan.provider_plan_id,billing_period=new_plan.billing_period
        )

        cancel_response = result["cancelled_subscription"]
        new_subscription = result["new_subscription"]

        # Extract customer_id
        customer_id = cancel_response.get("customer_id")

        # Ensure metadata exists
        if subscription.subscription_metadata is None:
            subscription.subscription_metadata = {}

        subscription.subscription_metadata["customer_id"] = customer_id

        # Mark old subscription to cancel
        subscription.cancel_at_period_end = True
        subscription.next_plan_id = new_plan.id

        # Save new Razorpay subscription id
        subscription.subscription_metadata["new_subscription_id"] = new_subscription["id"]

        db.session.commit()

        return {
            "message": "Subscription update initiated. Complete payment for the new plan.",
            "subscription_id": new_subscription["id"],
            "amount": new_plan.price,
            "currency": new_plan.currency,
            "plan_id": new_plan.id,
            "plan_name": new_plan.display_name,
            "user_email": current_user.email,
            "user_phone": current_user.phone,
            "key_id": VoiceSettings.RAZOR_KEY_ID
        }

    except HTTPException as e:
        raise e

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    

@router.post("/pause", dependencies=[Depends(security)], openapi_extra={"security": [{"BearerAuth": []}]})
def pause_subscription(data: SubscriptionPauseRequest, current_user: UnifiedAuthModel = Depends(get_current_user)):
    """
    Pause the active subscription.
    """
    try:
        subscription = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == current_user.id,
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).first()

        if not subscription:
            raise HTTPException(status_code=404, detail="No active subscription found")

        provider = PaymentProviderFactory.get_provider(subscription.provider)
        response = provider.pause_subscription(subscription.provider_subscription_id, data.pause_at)
        #change the state in database also if  pause_at is now
        if data.pause_at == "now":
            subscription.status = response.get("status")
            db.session.commit()

        return {"message": "Subscription pause initiated", "provider_response": response}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error pausing subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/resume", dependencies=[Depends(security)], openapi_extra={"security": [{"BearerAuth": []}]})
def resume_subscription(current_user: UnifiedAuthModel = Depends(get_current_user)):
    """
    Resume a paused subscription.
    """
    try:
        subscription = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == current_user.id
        ).order_by(UserSubscriptionModel.created_at.desc()).first()

        if not subscription:
            raise HTTPException(status_code=404, detail="No subscription found")
        if subscription.status == SubscriptionStatusEnum.cancelled:
            raise HTTPException(status_code=400, detail="Subscription is cancelled")
        if subscription.status == SubscriptionStatusEnum.active:
            raise HTTPException(status_code=400, detail="Subscription is already active")

        provider = PaymentProviderFactory.get_provider(subscription.provider)
        response = provider.resume_subscription(subscription.provider_subscription_id)
        subscription.status = response.get("status")
        db.session.commit()

        return {"message": "Subscription resume initiated", "provider_response": response}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error resuming subscription: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/invoices", response_model=InvoiceListResponse, dependencies=[Depends(security)], openapi_extra={"security": [{"BearerAuth": []}]})
def fetch_invoices(current_user: UnifiedAuthModel = Depends(get_current_user)):
    """
    Fetch invoices for the user's subscriptions.
    """
    try:
        subscriptions = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == current_user.id
        ).all()

        all_invoices = []
        for sub in subscriptions:
            provider = PaymentProviderFactory.get_provider(sub.provider)
            invoices = provider.get_subscription_invoices(sub.provider_subscription_id)
            
            for inv in invoices:
                all_invoices.append(InvoiceItemResponse(
                    id=inv.get("id"),
                    amount=float(inv.get("amount", 0)) / 100.0,
                    status=inv.get("status"),
                    date=inv.get("date"),
                    invoice_url=inv.get("short_url") or inv.get("invoice_url"),
                    description=inv.get("description")
                ))

        return InvoiceListResponse(invoices=all_invoices)

    except Exception as e:
        logger.error(f"Error fetching invoices: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))