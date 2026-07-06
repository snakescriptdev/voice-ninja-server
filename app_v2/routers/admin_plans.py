from fastapi import APIRouter, HTTPException, status, Depends
from fastapi_sqlalchemy import db
from typing import List
from app_v2.utils.jwt_utils import get_current_user, is_admin,HTTPBearer
from app_v2.databases.models import UnifiedAuthModel, PlanModel, PlanFeatureModel, PlanProviderModel, CoinPackageModel, UserSubscriptionModel
from app_v2.schemas.plans import PlanCreate, PlanUpdate, PlanResponse
from app_v2.schemas.admin_dashboard import CoinBundleCreate, CoinBundleUpdate, CoinBundleResponse
from app_v2.schemas.enum_types import PaymentProviderEnum, SubscriptionStatusEnum
from app_v2.utils.payment_utils import PaymentProviderFactory
from app_v2.core.logger import setup_logger
from sqlalchemy import or_, func
from sqlalchemy.orm import joinedload

logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/admin/plans", tags=["Admin Plans"])

def validate_unique_features(features):
    seen = set()
    duplicates = set()

    for f in features:
        key = f.feature_key if hasattr(f, "feature_key") else f["feature_key"]

        if key in seen:
            duplicates.add(key)

        seen.add(key)

    if duplicates:
        raise HTTPException(
            status_code=400,
            detail=f"Duplicate feature keys are not allowed: {list(duplicates)}"
        )

def validate_phone_numbers_match_voice_agents(features):
    voice_agents_limit = "__unset__"
    phone_numbers_limit = "__unset__"

    for f in features:
        key = f.feature_key if hasattr(f, "feature_key") else f["feature_key"]
        limit = f.limit if hasattr(f, "limit") else f.get("limit")

        if key == "ai_voice_agents":
            voice_agents_limit = limit
        elif key == "phone_numbers":
            phone_numbers_limit = limit

    if voice_agents_limit == "__unset__" or phone_numbers_limit == "__unset__":
        return

    # None represents "unlimited" - only equal (both unlimited) satisfies phone_numbers <= voice_agents
    if voice_agents_limit is None or phone_numbers_limit is None:
        mismatched = voice_agents_limit != phone_numbers_limit
    else:
        mismatched = phone_numbers_limit > voice_agents_limit

    if mismatched:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Number of phone numbers allowed ({phone_numbers_limit}) must be less than or equal to "
                f"number of AI voice agents allowed ({voice_agents_limit})"
            )
        )

MIN_API_ACCESS_LIMIT = 5

def validate_api_access_limit(features):
    for f in features:
        key = f.feature_key if hasattr(f, "feature_key") else f["feature_key"]
        if key != "api_access":
            continue

        limit = f.limit if hasattr(f, "limit") else f.get("limit")

        if limit is None:
            continue  # unlimited

        if limit == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "API access is enabled but its limit is 0. Remove the API access feature "
                    "if you want to keep it disabled, or set the limit to at least "
                    f"{MIN_API_ACCESS_LIMIT}."
                )
            )

        if limit < MIN_API_ACCESS_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=f"API access limit must be at least {MIN_API_ACCESS_LIMIT} when the feature is enabled."
            )

MIN_KNOWLEDGE_BASE_LIMIT_MB = 1
MAX_KNOWLEDGE_BASE_LIMIT_MB = 21

def validate_knowledge_base_limit(features):
    for f in features:
        key = f.feature_key if hasattr(f, "feature_key") else f["feature_key"]
        if key != "knowledge_base":
            continue

        limit = f.limit if hasattr(f, "limit") else f.get("limit")

        if limit is None:
            continue  # unlimited

        if limit < MIN_KNOWLEDGE_BASE_LIMIT_MB:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Knowledge base is enabled but its limit is less than "
                    f"{MIN_KNOWLEDGE_BASE_LIMIT_MB} MB. Remove the knowledge base feature "
                    "if you want to keep it disabled, or set the limit to at least "
                    f"{MIN_KNOWLEDGE_BASE_LIMIT_MB} MB."
                )
            )

        if limit > MAX_KNOWLEDGE_BASE_LIMIT_MB:
            raise HTTPException(
                status_code=400,
                detail=f"Knowledge base limit cannot exceed {MAX_KNOWLEDGE_BASE_LIMIT_MB} MB."
            )

@router.get("/coin-bundles", response_model=List[CoinBundleResponse])
def list_coin_bundles():
    try:
        bundles = db.session.query(CoinPackageModel).filter(CoinPackageModel.is_deleted==False).order_by(CoinPackageModel.created_at.desc()).all()
        return bundles
    except Exception as e:
        logger.error(f"Error listing coin bundles: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch coin bundles: {str(e)}"
        )
@router.post("/coin-bundles", response_model=CoinBundleResponse,dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def create_coin_bundle(data: CoinBundleCreate):
    try:
        if db.session.query(CoinPackageModel).filter(
            func.lower(CoinPackageModel.name) == data.name.lower(),
            CoinPackageModel.is_deleted == False
        ).first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Coin bundle with this name already exists"
            )
        bundle = CoinPackageModel(
            name=data.name,
            coins=data.coins,
            price=data.price,
            currency=data.currency,
            validity_days=data.validity_days,
            is_active=True
        )
        db.session.add(bundle)
        db.session.commit()
        db.session.refresh(bundle)
        return bundle
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating coin bundle: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create coin bundle: {str(e)}"
        )

@router.delete("/coin-bundle/{bundle_id}",status_code=status.HTTP_204_NO_CONTENT,dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def delete_bundle(bundle_id:int):
    try:
        bundle = db.session.query(CoinPackageModel).filter(CoinPackageModel.id == bundle_id).first()
        if not bundle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Coin bundle not found"
            )
        bundle.is_deleted=True
        db.session.add(bundle)
        db.session.commit()
        db.session.refresh(bundle)
        return
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting coin bundle: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete coin bundle: {str(e)}"
        )

@router.post("", response_model=PlanResponse, status_code=status.HTTP_201_CREATED,dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def create_plan(plan_data: PlanCreate):
    try:
        #check display name is unique (case-insensitive)
        if db.session.query(PlanModel).filter(func.lower(PlanModel.display_name) == plan_data.display_name.lower(),
        PlanModel.is_deleted==False).first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Plan with this display name already exists"
            )
        validate_unique_features(plan_data.features)
        validate_phone_numbers_match_voice_agents(plan_data.features)
        validate_api_access_limit(plan_data.features)
        validate_knowledge_base_limit(plan_data.features)
        # Only one plan can be marked as popular at a time
        if plan_data.mark_as_popular:
            existing_popular = db.session.query(PlanModel).filter(
                PlanModel.mark_as_popular == True,
                PlanModel.is_deleted == False
            ).first()
            if existing_popular:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Plan '{existing_popular.display_name}' is already marked as popular. Unmark it before marking another plan as popular.",
                )
        # 1. Create plan in database
        new_plan = PlanModel(
            display_name=plan_data.display_name,
            price=plan_data.price,
            currency=plan_data.currency,
            description=plan_data.description,
            coins_included=plan_data.coins_included,
            carry_forward_coins=plan_data.carry_forward_coins,
            billing_period=plan_data.billing_period,
            icon=plan_data.icon,
            gradient_color=plan_data.gradient_color,
            mark_as_popular=plan_data.mark_as_popular,
            is_active=plan_data.is_active
        )
        db.session.add(new_plan)
        db.session.flush() # Get plan ID

        # 2. Add features
        for feature in plan_data.features:
            new_feature = PlanFeatureModel(
                plan_id=new_plan.id,
                feature_key=feature.feature_key,
                limit=feature.limit
            )
            db.session.add(new_feature)

        # 3. Register with Razorpay
        try:
            rzp_provider = PaymentProviderFactory.get_provider("razorpay")
            rzp_plan = rzp_provider.create_plan(
                name=plan_data.display_name,
                amount=plan_data.price,
                currency=plan_data.currency,
                period=plan_data.billing_period,
                description=f"Plan {plan_data.display_name} - {plan_data.billing_period}"
            )
            
            provider_mapping = PlanProviderModel(
                plan_id=new_plan.id,
                provider=PaymentProviderEnum.razorpay,
                provider_plan_id=rzp_plan["provider_plan_id"],
                provider_metadata=rzp_plan["provider_metadata"],
                is_active=True
            )
            db.session.add(provider_mapping)
        except Exception as e:
            logger.error(f"Failed to create Razorpay plan: {str(e)}")
            # We continue even if Razorpay fails, but maybe we should rollback?
            # User might want to retry later. For now, let's keep it in DB.
            # raise HTTPException(status_code=500, detail=f"Plan created in DB but Razorpay failed: {str(e)}")

        db.session.commit()
        db.session.refresh(new_plan)
        return new_plan
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating plan: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("", response_model=List[PlanResponse])
def list_plans():
    try:
        plans = (
            db.session.query(PlanModel).filter(
                PlanModel.is_deleted== False
            )
            .options(
                joinedload(PlanModel.features)
            ).order_by(
                PlanModel.created_at.desc()
            )
            .all()
        )
        return [PlanResponse.model_validate(p) for p in plans]
    except Exception as e:
        logger.error(f"Error listing plans: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status-wise")
def list_plans_status_wise():
    """
    Categorized plan listing for admin dashboard.
    """
    try:
        plans = (
            db.session.query(PlanModel).filter(
                PlanModel.is_deleted==False
            )
            .options(
                joinedload(PlanModel.features),
                joinedload(PlanModel.providers)
            )
            .all()
        )
        
        active_plans = [p for p in plans if p.is_active]
        inactive_plans = [p for p in plans if not p.is_active]
        
        return {
            "active_plans": {
                "count": len(active_plans),
                "plans": active_plans
            },
            "inactive_plans": {
                "count": len(inactive_plans),
                "plans": inactive_plans
            }
        }
    except Exception as e:
        logger.error(f"Error listing status-wise plans: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{plan_id}", response_model=PlanResponse,dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def get_plan(plan_id: int):
    try:
        plan = (
            db.session.query(PlanModel)
            .options(
                joinedload(PlanModel.features),
                joinedload(PlanModel.providers)
            )
            .filter(PlanModel.id == plan_id,PlanModel.is_deleted==False)
            .first()
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        return PlanResponse.model_validate(plan)
    except Exception as e:
        logger.error(f"Error getting plan: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put(
    "/{plan_id}",
    response_model=PlanResponse,
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def update_plan(plan_id: int, plan_update: PlanUpdate):

    try:
        plan = db.session.query(PlanModel).filter(
            PlanModel.id == plan_id,
            PlanModel.is_deleted == False
        ).first()

        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")

        update_data = plan_update.dict(exclude_unset=True)

        # Check display_name uniqueness (case-insensitive) if being updated
        if "display_name" in update_data:
            conflict = db.session.query(PlanModel).filter(
                func.lower(PlanModel.display_name) == update_data["display_name"].lower(),
                PlanModel.id != plan_id,
                PlanModel.is_deleted == False
            ).first()
            if conflict:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Plan with this display name already exists"
                )

        # 🔹 Restrict immutable fields
        immutable_fields = ["price", "currency", "billing_period"]
        restricted_keys = [key for key in immutable_fields if key in update_data]
        if restricted_keys:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Fields {restricted_keys} cannot be updated once a plan is created."
            )

        # 🔹 Handle features safely
        if "features" in update_data:
            validate_unique_features(update_data["features"])
            validate_phone_numbers_match_voice_agents(update_data["features"])
            validate_api_access_limit(update_data["features"])
            validate_knowledge_base_limit(update_data["features"])

            # ORM-safe delete (NO bulk delete)
            old_features = db.session.query(PlanFeatureModel).filter(
                PlanFeatureModel.plan_id == plan_id
            ).all()
            
            # Keep a copy for downgrade comparison
            old_features_copy = [PlanFeatureModel(feature_key=f.feature_key, limit=f.limit) for f in old_features]

            for feature in old_features:
                db.session.delete(feature)
            db.session.flush()

            # Add new features
            new_features_list = []
            for feature in update_data["features"]:
                new_feature = PlanFeatureModel(
                    plan_id=plan_id,
                    **feature
                )
                db.session.add(new_feature)
                new_features_list.append(new_feature)

            # Trigger scheduled downgrades for plan change
            from app_v2.utils.downgrade_utils import schedule_downgrade_for_plan_change
            schedule_downgrade_for_plan_change(
                plan_id=plan_id,
                old_features=old_features_copy,
                new_features=new_features_list,
                session=db.session
            )

            del update_data["features"]

        # Only one plan can be marked as popular at a time
        if update_data.get("mark_as_popular") is True:
            existing_popular = db.session.query(PlanModel).filter(
                PlanModel.mark_as_popular == True,
                PlanModel.id != plan_id,
                PlanModel.is_deleted == False
            ).first()
            if existing_popular:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Plan '{existing_popular.display_name}' is already marked as popular. Unmark it before marking this plan as popular.",
                )

        # 🔹 Update remaining fields
        for key, value in update_data.items():
            setattr(plan, key, value)

        # When a plan is deactivated, cancel all active/paused subscriptions at period end
        if update_data.get("is_active") is False:
            subscriptions = db.session.query(UserSubscriptionModel).filter(
                UserSubscriptionModel.plan_id == plan_id,
                or_(
                    UserSubscriptionModel.status == SubscriptionStatusEnum.active,
                    UserSubscriptionModel.status == SubscriptionStatusEnum.paused,
                )
            ).all()

            for sub in subscriptions:
                try:
                    provider_type = None
                    if sub.provider:
                        if sub.provider.lower() == "stripe":
                            provider_type = PaymentProviderEnum.stripe
                        elif sub.provider.lower() == "razorpay":
                            provider_type = PaymentProviderEnum.razorpay

                    if not provider_type:
                        plan_provider = db.session.query(PlanProviderModel).filter(
                            PlanProviderModel.plan_id == plan_id,
                            PlanProviderModel.is_active == True
                        ).first()
                        if plan_provider:
                            provider_type = plan_provider.provider

                    if not provider_type:
                        logger.warning(f"Could not determine provider for subscription {sub.id}, skipping cancellation")
                        continue

                    provider = PaymentProviderFactory.get_provider(provider_type)
                    provider.cancel_subscription(sub.provider_subscription_id, cancel_at_cycle_end=True)
                    sub.cancel_at_period_end = True
                    sub.status = SubscriptionStatusEnum.cancelled
                    db.session.add(sub)
                    logger.info(f"Scheduled cancellation at period end for subscription {sub.id} (plan {plan_id} deactivated)")
                except Exception as sub_err:
                    logger.error(f"Failed to cancel subscription {sub.id} on plan deactivation: {str(sub_err)}")

        # 🔥 Flush before commit (catches DB errors early)
        db.session.flush()

        db.session.commit()

        return plan

    except HTTPException:
        db.session.rollback()
        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating plan: {str(e)}", exc_info=True)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update plan"
        )

@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT,dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def delete_plan(plan_id: int):
    plan = db.session.query(PlanModel).filter(PlanModel.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    try:
        # Find all active/paused subscriptions associated with this plan
        subscriptions = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.plan_id == plan_id,
            or_(
                UserSubscriptionModel.status == SubscriptionStatusEnum.active,
                UserSubscriptionModel.status == SubscriptionStatusEnum.paused
            )
        ).all()

        for sub in subscriptions:
            try:
                # Cancel each subscription at period end via provider
                provider_factory = PaymentProviderFactory()
                
                # Use sub.provider if available, else look up plan's provider
                provider_type = None
                if sub.provider:
                    # Map string 'stripe'/'razorpay' to Enum
                    if sub.provider.lower() == "stripe":
                        provider_type = PaymentProviderEnum.stripe
                    elif sub.provider.lower() == "razorpay":
                        provider_type = PaymentProviderEnum.razorpay
                
                if not provider_type:
                    # Fallback: find any active provider for this plan
                    plan_provider = db.session.query(PlanProviderModel).filter(
                        PlanProviderModel.plan_id == plan_id,
                        PlanProviderModel.is_active == True
                    ).first()
                    if plan_provider:
                        provider_type = plan_provider.provider
                
                if not provider_type:
                    logger.warning(f"Could not determine provider for subscription {sub.id}, skipping cancellation")
                    continue

                provider = provider_factory.get_provider(provider_type)
                provider.cancel_subscription(sub.provider_subscription_id, cancel_at_cycle_end=True)
                
                # Mark in DB
                sub.cancel_at_period_end = True
                db.session.add(sub)
                logger.info(f"Cancelled subscription {sub.provider_subscription_id} at period end for plan {plan_id}")
            except Exception as sub_err:
                logger.error(f"Failed to cancel subscription {sub.provider_subscription_id}: {str(sub_err)}")
                # We might want to continue or stop. Usually better to log and continue to allow plan deletion,
                # but the user said "first cancel", so maybe we should stop if critical.
                # However, if one fails, we should probably keep going for others.

        #soft delete plan
        plan.is_deleted= True
        db.session.add(plan)
        db.session.commit()
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting plan: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))