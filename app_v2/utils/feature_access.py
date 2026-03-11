from fastapi import HTTPException, status, Depends
from typing import Optional, Callable, Dict
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_

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
    ConversationsModel,
)

from app_v2.schemas.enum_types import SubscriptionStatusEnum, PhoneNumberAssignStatus
from app_v2.core.logger import setup_logger
from app_v2.utils.jwt_utils import get_current_user


logger = setup_logger(__name__)


# ------------------------------------------------------------------
# CANONICAL SUBSCRIPTION LOOKUP
# ------------------------------------------------------------------

def _get_active_subscription(user_id: int) -> Optional[UserSubscriptionModel]:
    """
    Return the single canonical active/paused subscription for a user.

    Rules (mirrors subscriptions.py _get_current_subscription):
      • status is active OR paused
      • cancel_at_period_end is False  — the row is not pending cancel/update
      • order by created_at desc as tiebreaker (should only ever be one)

    Why cancel_at_period_end=False matters:
      After update_subscription() is called, the existing row is stamped with
      cancel_at_period_end=True + pending_provider_subscription_id.  It is
      still technically active for the current cycle, but we treat the plan
      on that row as the authoritative plan until verify() swaps it.  If we
      did NOT filter cancel_at_period_end here we might pick up the old row
      after verify() has already created the clean state, especially in a
      race condition window.

      The side-effect is that during the checkout window (between update and
      verify) _get_active_subscription returns None, which means feature
      checks will 403.  To keep access alive during that window we fall back
      to allowing the old subscription even when cancel_at_period_end=True
      (see check_feature_limit_and_usage below).
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.user_id == user_id,
            or_(
                UserSubscriptionModel.status == SubscriptionStatusEnum.active,
                UserSubscriptionModel.status == SubscriptionStatusEnum.paused,
            ),
            UserSubscriptionModel.cancel_at_period_end == False,
        )
        .order_by(UserSubscriptionModel.created_at.desc())
        .first()
    )


def _get_any_active_subscription(user_id: int) -> Optional[UserSubscriptionModel]:
    """
    Looser lookup used ONLY for feature access checks.
    Includes subscriptions where cancel_at_period_end=True so that users
    retain access to their current plan's features during a plan-change
    checkout window (between /update and /verify).
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.user_id == user_id,
            or_(
                UserSubscriptionModel.status == SubscriptionStatusEnum.active,
                UserSubscriptionModel.status == SubscriptionStatusEnum.paused,
            ),
        )
        .order_by(
            # prefer non-pending rows first, then newest
            UserSubscriptionModel.cancel_at_period_end.asc(),
            UserSubscriptionModel.created_at.desc(),
        )
        .first()
    )


# ------------------------------------------------------------------
# PLAN FETCHING
# ------------------------------------------------------------------

def get_user_active_plan(user_id: int) -> Optional[PlanModel]:
    """Retrieve the active plan for a user."""
    with db():
        subscription = _get_active_subscription(user_id)
        if subscription:
            return db.session.query(PlanModel).filter(
                PlanModel.id == subscription.plan_id
            ).first()
        return None


# ------------------------------------------------------------------
# USAGE CALCULATION FUNCTIONS
# ------------------------------------------------------------------

def get_ai_voice_agents_usage(user_id: int) -> int:
    """Count only ENABLED agents — disabled ones don't count toward the limit."""
    return (
        db.session.query(func.count(AgentModel.id))
        .filter(
            AgentModel.user_id == user_id,
            AgentModel.is_enabled == True,
        )
        .scalar() or 0
    )


def get_web_agents_usage(user_id: int) -> int:
    """Count only ENABLED web agents."""
    return (
        db.session.query(func.count(WebAgentModel.id))
        .filter(
            WebAgentModel.user_id == user_id,
            WebAgentModel.is_enabled == True,
        )
        .scalar() or 0
    )


def get_phone_numbers_usage(user_id: int) -> int:
    """Count only phone numbers that are assigned (not unassigned/released)."""
    return (
        db.session.query(func.count(PhoneNumberService.id))
        .filter(
            PhoneNumberService.user_id == user_id,
            PhoneNumberService.status != PhoneNumberAssignStatus.unassigned,
        )
        .scalar() or 0
    )


def get_custom_voice_usage(user_id: int) -> int:
    return (
        db.session.query(func.count(VoiceModel.id))
        .filter(
            VoiceModel.user_id == user_id,
            VoiceModel.is_custom_voice == True,
        )
        .scalar() or 0
    )


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
    Check if user has access to a feature and if their usage is within the limit.

    Uses the looser _get_any_active_subscription so that feature access is
    preserved during the plan-change checkout window (after /update, before
    /verify).
    """
    with db():
        subscription = _get_any_active_subscription(user_id)

        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Active subscription required to access feature: {feature_key}",
            )

        feature = db.session.query(PlanFeatureModel).filter(
            PlanFeatureModel.plan_id == subscription.plan_id,
            PlanFeatureModel.feature_key == feature_key,
        ).first()

        plan = db.session.query(PlanModel).filter(
            PlanModel.id == subscription.plan_id
        ).first()

        plan_name = plan.display_name if plan else "Unknown Plan"

        if not feature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your current plan '{plan_name}' does not include access to {feature_key}.",
            )

        # Boolean feature (NULL limit = unlimited)
        if feature.limit is None:
            return True

        usage_handler = FEATURE_USAGE_HANDLERS.get(feature_key)
        if not usage_handler:
            return True

        current_usage = usage_handler(user_id)

        logger.info(
            f"Feature usage check | user={user_id} "
            f"feature={feature_key} "
            f"usage={current_usage} "
            f"limit={feature.limit}"
        )

        if current_usage >= feature.limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"You have reached the limit of {feature.limit} for "
                    f"{feature_key} on your current plan. Please upgrade to continue."
                ),
            )

        return True


def get_feature_limit(user_id: int, feature_key: str) -> Optional[float]:
    """
    Get the numeric limit for a feature from the user's active plan.
    Returns None if unlimited or feature not in plan.
    """
    with db():
        subscription = _get_any_active_subscription(user_id)
        if not subscription:
            return None

        feature = db.session.query(PlanFeatureModel).filter(
            PlanFeatureModel.plan_id == subscription.plan_id,
            PlanFeatureModel.feature_key == feature_key,
        ).first()

        if not feature:
            return None

        return float(feature.limit) if feature.limit is not None else None


def get_feature_usage(user_id: int, feature_key: str) -> float:
    """Calculate current usage for a feature."""
    usage_handler = FEATURE_USAGE_HANDLERS.get(feature_key)
    if not usage_handler:
        return 0.0

    with db():
        return usage_handler(user_id)


# ------------------------------------------------------------------
# FASTAPI DEPENDENCY
# ------------------------------------------------------------------

class RequireFeature:
    """FastAPI Dependency for requiring a feature and checking limits."""

    def __init__(self, feature_key: str):
        self.feature_key = feature_key

    def __call__(self, current_user: UnifiedAuthModel = Depends(get_current_user)):
        check_feature_limit_and_usage(current_user.id, self.feature_key)
        return current_user