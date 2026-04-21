from fastapi import APIRouter, HTTPException, status, Depends
from app_v2.utils.jwt_utils import is_admin,HTTPBearer
from datetime import datetime
from typing import List, Literal
from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel, ConversationsModel, CoinPackageModel, CoinUsageSettingsModel, PlanModel, UserSubscriptionModel, SubscriptionStatusEnum, PaymentModel, PaymentStatusEnum, CoinsLedgerModel, CoinTransactionTypeEnum, APICallLogModel
from app_v2.schemas.activity_schema import ActivityLogResponse
from app_v2.schemas.admin_dashboard import UserCostItem, CoinBundleCreate, CoinBundleResponse
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.core.logger import setup_logger
from fastapi_sqlalchemy import db
from sqlalchemy import func
from app_v2.utils.time_utils import format_time_ago
from app_v2.utils.analytics_utils import calculate_percentage_change, get_current_and_previous_month_start
from elevenlabs import ElevenLabs
from app_v2.core.config import VoiceSettings
from elevenlabs import ElevenLabs
from datetime import datetime, timezone
from sqlalchemy import select, func

client = ElevenLabs(api_key=VoiceSettings.ELEVENLABS_API_KEY)
logger = setup_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/v2/admin/dashboard",tags=["Admin"],dependencies=[Depends(security)])

@router.get("/overview/stats",dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def get_overview_stats():
    """
    Consolidated API for admin dashboard overview stats.
    """
    try:
        first_day_of_month, first_day_prev_month = get_current_and_previous_month_start()

        # 1. Total Users
        total_users = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.is_admin.is_(False)).count()
        curr_users_new = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.is_admin.is_(False),
            UnifiedAuthModel.created_at >= first_day_of_month
        ).count()
        prev_users_new = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.is_admin.is_(False),
            UnifiedAuthModel.created_at >= first_day_prev_month,
            UnifiedAuthModel.created_at < first_day_of_month
        ).count()
        total_users_change = calculate_percentage_change(curr_users_new, prev_users_new)

        # 2. Active Subscriptions
        active_subscriptions = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).count()
        curr_subs_new = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.status == SubscriptionStatusEnum.active,
            UserSubscriptionModel.current_period_start >= first_day_of_month
        ).count()
        prev_subs_new = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.status == SubscriptionStatusEnum.active,
            UserSubscriptionModel.current_period_start >= first_day_prev_month,
            UserSubscriptionModel.current_period_start < first_day_of_month
        ).count()
        active_subscriptions_change = calculate_percentage_change(curr_subs_new, prev_subs_new)

        # 3. Total Phone Numbers
        total_phone_numbers = db.session.query(PhoneNumberService).count()

        # 4. Agent Stats
        agent_stats_query = db.session.query(
            AgentModel.is_enabled,
            func.count(AgentModel.id).label("count")
        ).group_by(AgentModel.is_enabled).all()

        active_agents = 0
        disabled_agents = 0
        for is_enabled, count in agent_stats_query:
            if is_enabled is True:
                active_agents = count
            else:
                disabled_agents = count
        total_agents = active_agents + disabled_agents

        # 5. Total Coins Distributed
        total_coins_distributed = db.session.query(func.sum(CoinsLedgerModel.coins)).filter(
            CoinsLedgerModel.coins > 0
        ).scalar() or 0

        # 6. Current Month Revenue
        current_month_revenue = db.session.query(func.sum(PaymentModel.amount)).filter(
            PaymentModel.status == PaymentStatusEnum.success,
            PaymentModel.created_at >= first_day_of_month
        ).scalar() or 0
        prev_month_revenue = db.session.query(func.sum(PaymentModel.amount)).filter(
            PaymentModel.status == PaymentStatusEnum.success,
            PaymentModel.created_at >= first_day_prev_month,
            PaymentModel.created_at < first_day_of_month
        ).scalar() or 0
        current_month_revenue_change = calculate_percentage_change(current_month_revenue, prev_month_revenue)

        # 7. Total API Hits
        total_api_hits = db.session.query(func.count(APICallLogModel.id)).scalar() or 0
        curr_api_hits = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.created_at >= first_day_of_month
        ).scalar() or 0
        prev_api_hits = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.created_at >= first_day_prev_month,
            APICallLogModel.created_at < first_day_of_month
        ).scalar() or 0
        total_api_hits_change = calculate_percentage_change(curr_api_hits, prev_api_hits)

        return {
            "status": "success",
            "stats": {
                "total_users": total_users,
                "total_users_change": float(total_users_change),
                "active_subscriptions": active_subscriptions,
                "active_subscriptions_change": float(active_subscriptions_change),
                "total_phone_numbers": total_phone_numbers,
                "total_agents": total_agents,
                "active_agents": active_agents,
                "disabled_agents": disabled_agents,
                "total_coins_distributed": int(total_coins_distributed),
                "current_month_revenue": float(current_month_revenue),
                "current_month_revenue_change": float(current_month_revenue_change),
                "total_api_hits": total_api_hits,
                "total_api_hits_change": float(total_api_hits_change)
            }
        }
    except Exception as e:
        logger.error(f"Error in get_overview_stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/overview/recent-users",dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
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
                "registered_at": format_time_ago(user.created_at) if user.created_at else "long time ago"
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

@router.get("/analytics/revenue-graph",dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def get_revenue_graph():
    """
    Monthly revenue for the last 6 months.
    """
    try:
        now = datetime.now(timezone.utc)
        year = now.year
        month = now.month
        # Calculate 5 months ago to get a total of 6 months including current
        for _ in range(5):
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        six_months_ago = datetime(year, month, 1)
        
        revenue_query = db.session.query(
            func.to_char(PaymentModel.created_at, 'YYYY-MM').label('month'),
            func.sum(PaymentModel.amount).label('revenue')
        ).filter(
            PaymentModel.status == PaymentStatusEnum.success,
            PaymentModel.created_at >= six_months_ago
        ).group_by('month').order_by('month').all()

        return {
            "status": "success",
            "revenue_graph": [{"month": r.month, "revenue": float(r.revenue)} for r in revenue_query]
        }
    except Exception as e:
        logger.error(f"Error in get_revenue_graph: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/analytics/subscription-distribution",dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def get_subscription_distribution():
    """
    Subscription distribution by plan percentage.
    """
    try:
        total_active = db.session.query(UserSubscriptionModel).filter(
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).count()

        if total_active == 0:
            return {"status": "success", "distribution": []}

        distribution_query = db.session.query(
            PlanModel.display_name,
            func.count(UserSubscriptionModel.id).label('count')
        ).join(PlanModel, UserSubscriptionModel.plan_id == PlanModel.id).filter(
            UserSubscriptionModel.status == SubscriptionStatusEnum.active
        ).group_by(PlanModel.display_name).all()

        distribution = [
            {
                "plan_name": d.display_name,
                "count": d.count,
                "percentage": round((d.count / total_active) * 100, 2)
            } for d in distribution_query
        ]

        return {
            "status": "success",
            "total_active": total_active,
            "distribution": distribution
        }
    except Exception as e:
        logger.error(f"Error in get_subscription_distribution: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )





@router.get("/elevenlabs/usage-and-billing",dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
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

@router.get("/users-cost", response_model=PaginatedResponse[UserCostItem],dependencies=[Depends(is_admin)],openapi_extra={"security":[{"BearerAuth":[]}]})
def get_users_cost(
    cost_type: Literal["credits", "coins"] = "credits",
    skip: int = 0, 
    limit: int = 10
):
    try:
        first_day_of_month, _ = get_current_and_previous_month_start()
        
        if cost_type == "credits":
            # Aggregate cost per user
            cost_query = db.session.query(
                ConversationsModel.user_id,
                func.sum(ConversationsModel.cost).label("total_cost")
            ).filter(
                ConversationsModel.created_at >= first_day_of_month
            ).group_by(ConversationsModel.user_id).subquery()
        else:
            cost_query = db.session.query(
                CoinsLedgerModel.user_id,
                func.sum(func.abs(CoinsLedgerModel.coins)).label("total_cost")
            ).filter(
                CoinsLedgerModel.created_at >= first_day_of_month,
                CoinsLedgerModel.coins < 0
            ).group_by(CoinsLedgerModel.user_id).subquery()

        total_cost_col = func.coalesce(cost_query.c.total_cost, 0)

        # Join with UnifiedAuthModel to get user details
        query = db.session.query(
            UnifiedAuthModel.id.label("user_id"),
            UnifiedAuthModel.name,
            UnifiedAuthModel.username,
            UnifiedAuthModel.email,
            total_cost_col.label("total_cost")
        ).outerjoin(cost_query, UnifiedAuthModel.id == cost_query.c.user_id)

        # Order by total_cost DESC
        query = query.order_by(total_cost_col.desc())

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

