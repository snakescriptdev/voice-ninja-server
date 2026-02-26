from fastapi import APIRouter, HTTPException, status, Depends
from app_v2.utils.jwt_utils import is_admin
from datetime import datetime
from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel, ConversationsModel
from app_v2.schemas.activity_schema import ActivityLogResponse
from app_v2.schemas.admin_dashboard import UserCostItem
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.core.logger import setup_logger
from fastapi_sqlalchemy import db
from sqlalchemy import func
from app_v2.utils.time_utils import format_time_ago
from elevenlabs import ElevenLabs
from app_v2.core.config import VoiceSettings
from elevenlabs import ElevenLabs
from datetime import datetime, timezone

client = ElevenLabs(api_key=VoiceSettings.ELEVENLABS_API_KEY)



logger = setup_logger(__name__)


router = APIRouter(prefix="/api/v2/admin/dashboard",tags=["Admin"])

# ... (format_time_ago logic)







#overview page api's

@router.get("/overview/user-count")
def get_user_count():
    try:
        users = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.is_admin.is_(False)
        ).count()

    #will be updated to return users grouped by subscription Plan

        return {
            "status":"success",
            "user_count": users
        }
    except Exception as e:
        logger.error(f"Error in get_user_count: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/overview/recent-users")
def get_recent_users():
    try:
        recent_users = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.is_admin.is_(False)
        ).order_by(UnifiedAuthModel.created_at.desc()).limit(5).all()

        users_data = []
        for user in recent_users:
            users_data.append({
                "id": user.id,
                "name": user.name or user.username or "Unknown",
                "email": user.email,
                "registered_at": format_time_ago(user.created_at) if user.created_at else "Unknown"
            })

        return {
            "status": "success",
            "recent_users": users_data
        }
    except Exception as e:
        logger.error(f"Error in get_recent_users: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/overview/phone-number-count")
def get_phone_number_count():
    try:
        phone_numbers = db.session.query(PhoneNumberService).count()

        return {
            "status":"success",
            "phone_number_count": phone_numbers
        }
    
    except Exception as e:
        logger.error(f"Error in get_phone_number_count: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/overview/agent-count")
def get_agent_count():
    try:
        agent_count_list = db.session.query(
            AgentModel.is_enabled,
            func.count(AgentModel.id).label("count")
        ).group_by(AgentModel.is_enabled).all()

        active_count = 0
        disabled_count = 0

        for is_enabled, count in agent_count_list:
            if is_enabled is True:
                active_count = count
            elif is_enabled is False:
                disabled_count = count

        total_count = active_count + disabled_count

        return {
            "total_agents": total_count,
            "active_agents": active_count,
            "disabled_agents": disabled_count
        }
    
    except Exception as e:
        logger.error(f"Error in get_agent_count: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )





@router.get("/elevenlabs/usage-and-billing")
def get_elevenlabs_usage_and_billing():
    try:
        # Fetch subscription from ElevenLabs
        subscription = client.user.subscription.get()

        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription information not found."
            )

        # Safely format reset time
        next_reset = None
        if getattr(subscription, "next_character_count_reset_unix", None):
            try:
                next_reset = datetime.fromtimestamp(
                    subscription.next_character_count_reset_unix,
                    tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                next_reset = None

        billing_summary = {
            "tier": getattr(subscription, "tier", None),
            "currency": getattr(subscription, "currency", None),
            "billing_period": getattr(subscription, "billing_period", None),
            "has_open_invoices": getattr(subscription, "has_open_invoices", None),
            "character_count": getattr(subscription, "character_count", 0),
            "character_limit": getattr(subscription, "character_limit", 0),
            "next_character_count_reset": next_reset,
        }

        # Handle next invoice safely
        if getattr(subscription, "next_invoice", None):
            inv = subscription.next_invoice

            next_payment_attempt = None
            if getattr(inv, "next_payment_attempt_unix", None):
                try:
                    next_payment_attempt = datetime.fromtimestamp(
                        inv.next_payment_attempt_unix,
                        tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M:%S %Z")
                except Exception:
                    next_payment_attempt = None

            billing_summary["next_invoice"] = {
                "amount_due_usd": (
                    inv.amount_due_cents / 100
                    if getattr(inv, "amount_due_cents", None)
                    else None
                ),
                "next_payment_attempt": next_payment_attempt,
            }
        else:
            billing_summary["next_invoice"] = None

        return {
            "status": "success",
            "subscription_billing": billing_summary,
        }

    except HTTPException:
        # Re-raise FastAPI HTTP exceptions
        raise

    except Exception as e:
        logger.error(f"Error fetching ElevenLabs billing info: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch usage and billing information from ElevenLabs."
        )

@router.get("/users-cost", response_model=PaginatedResponse[UserCostItem])
def get_users_cost(
    skip: int = 0, 
    limit: int = 10
):
    try:
        # Aggregate cost per user
        cost_query = db.session.query(
            ConversationsModel.user_id,
            func.sum(ConversationsModel.cost).label("total_cost")
        ).group_by(ConversationsModel.user_id).subquery()

        # Join with UnifiedAuthModel to get user details
        query = db.session.query(
            UnifiedAuthModel.id.label("user_id"),
            UnifiedAuthModel.name,
            UnifiedAuthModel.username,
            UnifiedAuthModel.email,
            func.coalesce(cost_query.c.total_cost, 0).label("total_cost")
        ).outerjoin(cost_query, UnifiedAuthModel.id == cost_query.c.user_id)

        # Order by total_cost DESC
        query = query.order_by(func.coalesce(cost_query.c.total_cost, 0).desc())

        # Total count for pagination
        total_count = query.count()

        # Apply pagination
        results = query.offset(skip).limit(limit).all()

        items = [
            UserCostItem(
                user_id=r.user_id,
                user_name=r.name or r.username or "Unknown",
                email=r.email or "",
                total_cost=float(r.total_cost)
            ) for r in results
        ]

        from math import ceil
        total_pages = ceil(total_count / limit) if limit > 0 else 1
        current_page = (skip // limit) + 1 if limit > 0 else 1

        return PaginatedResponse(
            total=total_count,
            page=current_page,
            size=limit,
            pages=total_pages,
            items=items
        )

    except Exception as e:
        logger.error(f"Error in get_users_cost: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch users cost data: {str(e)}"
        )