from fastapi import HTTPException, status, Depends
from typing import Optional, Callable, Dict
from fastapi_sqlalchemy import db
from sqlalchemy import func

from app_v2.databases.models import (
    UnifiedAuthModel,
    UserSubscriptionModel,
    PlanModel,
    PlanFeatureModel,
    AgentModel,
    WebAgentModel,
    KnowledgeBaseModel,
    PhoneNumberService,
    VoiceModel,
    ConversationsModel
)

from app_v2.schemas.enum_types import SubscriptionStatusEnum
from app_v2.core.logger import setup_logger
from app_v2.utils.jwt_utils import get_current_user


logger = setup_logger(__name__)


# ------------------------------------------------------------------
# PLAN FETCHING
# ------------------------------------------------------------------

def get_user_active_plan(user_id: int) -> Optional[PlanModel]:
    """Retrieve the active plan for a user."""
    with db():
        subscription = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).order_by(UserSubscriptionModel.created_at.desc()).first()

        if subscription:
            return db.session.query(PlanModel).filter(
                PlanModel.id == subscription.plan_id
            ).first()

        return None


# ------------------------------------------------------------------
# USAGE CALCULATION FUNCTIONS
# ------------------------------------------------------------------

def get_ai_voice_agents_usage(user_id: int) -> int:
    return db.session.query(func.count(AgentModel.id)) \
        .filter(AgentModel.user_id == user_id).scalar() or 0


def get_web_agents_usage(user_id: int) -> int:
    return db.session.query(func.count(WebAgentModel.id)) \
        .filter(WebAgentModel.user_id == user_id).scalar() or 0


def get_phone_numbers_usage(user_id: int) -> int:
    return db.session.query(func.count(PhoneNumberService.id)) \
        .filter(PhoneNumberService.user_id == user_id).scalar() or 0


def get_custom_voice_usage(user_id: int) -> int:
    return db.session.query(func.count(VoiceModel.id)) \
        .filter(
            VoiceModel.user_id == user_id,
            VoiceModel.is_custom_voice == True
        ).scalar() or 0


def get_kb_usage_mb(user_id: int) -> float:
    """
    Knowledge base limit is stored in MB.
    DB stores file_size in KB.
    """
    total_kb = db.session.query(
        func.coalesce(func.sum(KnowledgeBaseModel.file_size), 0)
    ).filter(
        KnowledgeBaseModel.user_id == user_id
    ).scalar()

    return float(total_kb) / 1024


def get_monthly_minutes_usage(user_id: int) -> float:
    """
    Monthly minutes limit stored in minutes.
    DB stores duration in seconds.
    """
    total_seconds = db.session.query(
        func.coalesce(func.sum(ConversationsModel.duration), 0)
    ).filter(
        ConversationsModel.user_id == user_id
    ).scalar()

    return float(total_seconds) / 60


# ------------------------------------------------------------------
# FEATURE → USAGE HANDLER MAP
# ------------------------------------------------------------------

FEATURE_USAGE_HANDLERS: Dict[str, Callable[[int], float]] = {
    "ai_voice_agents": get_ai_voice_agents_usage,
    "phone_numbers": get_phone_numbers_usage,
    "web_voice_agent": get_web_agents_usage,
    "knowledge_base": get_kb_usage_mb,
    "monthly_minutes": get_monthly_minutes_usage,
    "custom_voice_cloning": get_custom_voice_usage,
}


# ------------------------------------------------------------------
# MAIN FEATURE CHECKER
# ------------------------------------------------------------------

def check_feature_limit_and_usage(user_id: int, feature_key: str):
    """
    Check if user has access to feature and if usage limit exceeded.
    """

    with db():

        # -------------------------
        # Get active subscription
        # -------------------------
        subscription = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).order_by(UserSubscriptionModel.created_at.desc()).first()

        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Active subscription required to access feature: {feature_key}"
            )

        # -------------------------
        # Fetch feature from plan
        # -------------------------
        feature = db.session.query(PlanFeatureModel).filter(
            PlanFeatureModel.plan_id == subscription.plan_id,
            PlanFeatureModel.feature_key == feature_key
        ).first()

        plan = db.session.query(PlanModel).filter(
            PlanModel.id == subscription.plan_id
        ).first()

        plan_name = plan.display_name if plan else "Unknown Plan"

        if not feature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your current plan '{plan_name}' does not include access to {feature_key}."
            )

        # -------------------------
        # Boolean feature
        # -------------------------
        if feature.limit is None:
            return True

        # -------------------------
        # Get usage handler
        # -------------------------
        usage_handler = FEATURE_USAGE_HANDLERS.get(feature_key)

        if not usage_handler:
            # Feature without usage tracking (boolean access)
            return True

        # -------------------------
        # Calculate usage
        # -------------------------
        current_usage = usage_handler(user_id)

        logger.info(
            f"Feature usage check | user={user_id} "
            f"feature={feature_key} "
            f"usage={current_usage} "
            f"limit={feature.limit}"
        )

        # -------------------------
        # Limit validation
        # -------------------------
        if current_usage >= feature.limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"You have reached the limit of {feature.limit} for "
                    f"{feature_key} on your current plan. Please upgrade to continue."
                )
            )

        return True


def get_feature_limit(user_id: int, feature_key: str) -> Optional[float]:
    """
    Get the numeric limit for a feature from the user's plan.
    Returns None if no limit (unlimited) or if feature not in plan.
    """
    with db():
        subscription = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).order_by(UserSubscriptionModel.created_at.desc()).first()

        if not subscription:
            return None

        feature = db.session.query(PlanFeatureModel).filter(
            PlanFeatureModel.plan_id == subscription.plan_id,
            PlanFeatureModel.feature_key == feature_key
        ).first()

        if not feature:
            return None

        return float(feature.limit) if feature.limit is not None else None

def get_feature_usage(user_id: int, feature_key: str) -> float:
    """
    Calculate current usage for a feature.
    """
    usage_handler = FEATURE_USAGE_HANDLERS.get(feature_key)
    if not usage_handler:
        return 0.0
    
    with db():
        return usage_handler(user_id)


# ------------------------------------------------------------------
# FASTAPI DEPENDENCY
# ------------------------------------------------------------------

class RequireFeature:
    """
    FastAPI Dependency for requiring a feature and checking limits.
    """

    def __init__(self, feature_key: str):
        self.feature_key = feature_key

    def __call__(self, current_user: UnifiedAuthModel = Depends(get_current_user)):
        check_feature_limit_and_usage(current_user.id, self.feature_key)
        return current_user