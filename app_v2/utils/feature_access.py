from fastapi import HTTPException, status, Depends
from typing import Optional, Callable, Dict
from fastapi_sqlalchemy import db
from sqlalchemy import func
from datetime import datetime, timezone
from app_v2.databases.models import (
    UnifiedAuthModel,
    AgentModel,
    WidgetModel,
    KnowledgeBaseModel,
    PhoneNumberService,
    VoiceModel,
    ConversationsModel,
)

from app_v2.schemas.enum_types import PhoneNumberAssignStatus, PlanFeatureEnum
from app_v2.core.logger import setup_logger
from app_v2.utils.jwt_utils import get_current_user
from app_v2.utils.public_auth import get_public_api_user


logger = setup_logger(__name__)


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


def get_widgets_usage(user_id: int) -> int:
    """Count widgets."""
    return (
        db.session.query(func.count(WidgetModel.id))
        .filter(
            WidgetModel.user_id == user_id
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
    now = datetime.now(timezone.utc)
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
    "widget_agent": get_widgets_usage,
    "knowledge_base": get_kb_usage_mb,
    "monthly_minutes": get_monthly_minutes_usage,
    "custom_voice_cloning": get_custom_voice_usage,
}


# ------------------------------------------------------------------
# MAIN FEATURE CHECKER
# ------------------------------------------------------------------

def check_feature_limit_and_usage(user_id: int, feature_key: str, allow_coin_fallback: bool = False):
    """
    Check if user has access to a feature.

    Managing resources (create/edit/delete/view) is not coin-gated — only
    actually starting a call is (enforced separately at the websocket layer).
    `feature_key`/`allow_coin_fallback` are accepted for call-site
    compatibility but no longer change behavior.
    """
    return True


def get_feature_limit(user_id: int, feature_key: str) -> Optional[float]:
    """
    Get the numeric limit for a feature. Access is coin-gated now rather than
    plan-gated, so there is no count limit to enforce — always unlimited.
    """
    return None


def get_all_feature_limits(user_id: int) -> Optional[Dict[str, Optional[int]]]:
    """
    Get all feature limits. Access is coin-gated now rather than plan-gated,
    so every feature reports unlimited (None) rather than a plan-derived cap.
    """
    return {feature_key.value: None for feature_key in PlanFeatureEnum}


def get_feature_usage(user_id: int, feature_key: str) -> float:
    """Calculate current usage for a feature."""
    usage_handler = FEATURE_USAGE_HANDLERS.get(feature_key)
    if not usage_handler:
        return 0.0

    with db():
        return usage_handler(user_id)


def check_can_enable_resource(user_id: int, feature_key: str, allow_coin_fallback: bool = False):
    """
    Called specifically when a user tries to ENABLE an existing resource
    (agent, widget etc.) that is currently disabled.

    Enabling a resource is not coin-gated — only actually starting a call is
    (enforced separately at the websocket layer). `feature_key`/
    `allow_coin_fallback` are accepted for call-site compatibility but no
    longer change behavior.
    """
    return True


def require_feature_enabled(user_id: int, feature_key: str):
    """
    Check that a user can access a feature's management (view/create/edit/
    delete). Not coin-gated — only actually starting a call is (enforced
    separately at the websocket layer).
    """
    return True


# ------------------------------------------------------------------
# FASTAPI DEPENDENCY
# ------------------------------------------------------------------

class RequireFeature:
    """FastAPI Dependency for requiring a feature and checking limits."""

    def __init__(self, feature_key: str, allow_coin_fallback: bool = False):
        self.feature_key = feature_key
        self.allow_coin_fallback = allow_coin_fallback

    def __call__(self, current_user: UnifiedAuthModel = Depends(get_current_user)):
        if current_user.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account has been suspended. Please contact support for assistance.",
            )
        check_feature_limit_and_usage(current_user.id, self.feature_key, self.allow_coin_fallback)
        return current_user
class RequireFeaturePublic:
    """FastAPI Dependency for requiring a feature and checking limits (API Key-based)."""

    def __init__(self, feature_key: str,allow_coin_fallback: bool = False):
        self.feature_key = feature_key
        self.allow_coin_fallback = allow_coin_fallback
    def __call__(self, current_user: UnifiedAuthModel = Depends(get_public_api_user)):
        check_feature_limit_and_usage(current_user.id, self.feature_key, self.allow_coin_fallback)
        return current_user


class RequireFeatureEnabled:
    """FastAPI Dependency for requiring a feature to be present on the plan (no usage-limit check, JWT-based)."""

    def __init__(self, feature_key: str):
        self.feature_key = feature_key

    def __call__(self, current_user: UnifiedAuthModel = Depends(get_current_user)):
        if current_user.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account has been suspended. Please contact support for assistance.",
            )
        require_feature_enabled(current_user.id, self.feature_key)
        return current_user


class RequireFeatureEnabledPublic:
    """FastAPI Dependency for requiring a feature to be present on the plan (no usage-limit check, API Key-based)."""

    def __init__(self, feature_key: str):
        self.feature_key = feature_key

    def __call__(self, current_user: UnifiedAuthModel = Depends(get_public_api_user)):
        require_feature_enabled(current_user.id, self.feature_key)
        return current_user
