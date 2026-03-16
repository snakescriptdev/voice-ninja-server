from fastapi import HTTPException, status, Depends
from typing import Optional, Callable, Dict
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_
from datetime import datetime
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
# ACTIVE-LIKE STATUSES
# ------------------------------------------------------------------
# These are the statuses that grant feature access.
#
#   active        – subscription charged and confirmed by webhook
#   paused        – user-initiated pause, still within paid period
#   authenticated – mandate confirmed by Razorpay but subscription.charged
#                   webhook not yet received (window between /verify and the
#                   first webhook fire).  We grant access optimistically here
#                   because the user has completed checkout; charge failure
#                   after authentication is extremely rare and is handled by
#                   subscription.pending / halted events.
#
_ACTIVE_LIKE = (
    SubscriptionStatusEnum.active,
    SubscriptionStatusEnum.paused,
    SubscriptionStatusEnum.authenticated,
)


# ------------------------------------------------------------------
# CANONICAL SUBSCRIPTION LOOKUP
# ------------------------------------------------------------------

def _get_active_subscription(user_id: int) -> Optional[UserSubscriptionModel]:
    """
    Return the single canonical active/paused/authenticated subscription for a user.

    Rules:
      • status is active OR paused OR authenticated
      • cancel_at_period_end is False  — not pending cancel/update
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
      (see _get_any_active_subscription below).
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.status.in_(_ACTIVE_LIKE),
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

    Also includes authenticated status so users get feature access
    immediately after completing checkout, even before subscription.charged
    webhook fires (see module docstring above for rationale).

    Ordering:
      • Prefer non-pending rows first (cancel_at_period_end=False → 0 sorts first)
      • Then newest by created_at
    This means after verify() completes (cancel_at_period_end reset to False,
    status=authenticated) we always return that fresh row rather than any
    lingering old row.
    """
    return (
        db.session.query(UserSubscriptionModel)
        .filter(
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.status.in_(_ACTIVE_LIKE),
        )
        .order_by(
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
    """Count agents."""
    return (
        db.session.query(func.count(AgentModel.id))
        .filter(
            AgentModel.user_id == user_id
        )
        .scalar() or 0
    )


def get_web_agents_usage(user_id: int) -> int:
    """Count web agents."""
    return (
        db.session.query(func.count(WebAgentModel.id))
        .filter(
            WebAgentModel.user_id == user_id
        )
        .scalar() or 0
    )


def get_phone_numbers_usage(user_id: int) -> int:
    """Count phone numbers."""
    return (
        db.session.query(func.count(PhoneNumberService.id))
        .filter(
            PhoneNumberService.user_id == user_id
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
    Counts only conversations in the current calendar month.
    """
    now = datetime.utcnow()
    start_of_month = datetime(now.year, now.month, 1)

    total_seconds = (
        db.session.query(
            func.coalesce(func.sum(ConversationsModel.duration), 0)
        )
        .filter(
            ConversationsModel.user_id == user_id,
            ConversationsModel.created_at >= start_of_month
        )
        .scalar()
    )

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
    /verify) and also during the authenticated→charged webhook window.
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


def get_all_feature_limits(user_id: int) -> Optional[Dict[str, Optional[int]]]:
    """
    Get all feature limits for the user's active plan.
    Returns None if no active subscription.
    """
    with db():
        subscription = _get_any_active_subscription(user_id)
        if not subscription:
            return None

        features = db.session.query(PlanFeatureModel).filter(
            PlanFeatureModel.plan_id == subscription.plan_id
        ).all()

        return {
            f.feature_key: (int(f.limit) if f.limit is not None else None)
            for f in features
        }


def get_feature_usage(user_id: int, feature_key: str) -> float:
    """Calculate current usage for a feature."""
    usage_handler = FEATURE_USAGE_HANDLERS.get(feature_key)
    if not usage_handler:
        return 0.0

    with db():
        return usage_handler(user_id)


def check_can_enable_resource(user_id: int, feature_key: str):
    """
    Called specifically when a user tries to ENABLE an existing resource
    (agent, web agent etc.) that is currently disabled.

    Rule: enabled_count must be strictly less than the plan limit before
    allowing the enable action.

    This is separate from check_feature_limit_and_usage() which guards
    resource CREATION using total owned count.

    Usage:
        # In your agent enable endpoint, before setting is_enabled = True:
        check_can_enable_resource(current_user.id, "ai_voice_agents")

        # In your web agent enable endpoint:
        check_can_enable_resource(current_user.id, "web_voice_agent")
    """
    with db():
        # Use loose lookup so access is preserved during plan-change checkout
        # window and during the authenticated→charged webhook window.
        subscription = _get_any_active_subscription(user_id=user_id)

        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active subscription required.",
            )
        logger.info(f"Subscription: {subscription.id, subscription.plan_id}")

        feature = (
            db.session.query(PlanFeatureModel)
            .filter(
                PlanFeatureModel.plan_id == subscription.plan_id,
                PlanFeatureModel.feature_key == feature_key,
            )
            .first()
        )

        if not feature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your current plan does not include access to {feature_key}.",
            )

        # NULL limit means unlimited — always allow enable
        if feature.limit is None:
            return True

        # Count only currently ENABLED resources of this type
        enabled_count_handlers: Dict[str, Callable[[int], int]] = {
            "ai_voice_agents": lambda uid: (
                db.session.query(func.count(AgentModel.id))
                .filter(AgentModel.user_id == uid, AgentModel.is_enabled == True)
                .scalar() or 0
            ),
            "web_voice_agent": lambda uid: (
                db.session.query(func.count(WebAgentModel.id))
                .filter(WebAgentModel.user_id == uid, WebAgentModel.is_enabled == True)
                .scalar() or 0
            ),
        }

        handler = enabled_count_handlers.get(feature_key)
        if not handler:
            # Feature has no enabled-count concept (e.g. phone numbers, kb)
            return True

        enabled_count = handler(user_id)

        logger.info(
            f"Enable resource check | user={user_id} | "
            f"feature={feature_key} | "
            f"enabled={enabled_count} | limit={feature.limit}"
        )

        if enabled_count >= feature.limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"You already have {enabled_count} active {feature_key} "
                    f"which is the limit on your current plan. "
                    f"Disable an existing one before enabling another."
                ),
            )

        return True


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