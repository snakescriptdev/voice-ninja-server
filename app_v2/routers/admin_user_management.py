from fastapi import APIRouter, HTTPException, status, Depends, Query
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_, desc, select
from typing import List, Optional
from datetime import datetime, timedelta
from app_v2.databases.models import UnifiedAuthModel, UserSubscriptionModel, PlanModel, AgentModel, PhoneNumberService, CoinsLedgerModel, ActivityLogModel, APICallLogModel, SubscriptionStatusEnum
from app_v2.utils.jwt_utils import is_admin, HTTPBearer
from app_v2.schemas.admin_user_management import UserManagementStats, UserManagementListItem, SuspendUserRequest,AdjustUserCoinRequest
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.utils.time_utils import format_time_ago
from app_v2.core.logger import setup_logger
from app_v2.utils.payment_utils import PaymentProviderFactory

from app_v2.utils.coin_utils import admin_adjust_coins, get_user_coin_balance

security = HTTPBearer()
logger = setup_logger(__name__)
router = APIRouter(prefix="/api/v2/admin/user-management", tags=["Admin"],dependencies=[Depends(security),Depends(is_admin)])

@router.get("/stats", response_model=UserManagementStats,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_management_stats():
    """
    Get general user management statistics.
    """
    try:
        # Total users (non-admin)
        total_users = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.is_admin.is_(False)).count()

        # Users by plan
        plan_counts = db.session.query(
            PlanModel.display_name,
            func.count(UserSubscriptionModel.id).label("count")
        ).join(UserSubscriptionModel, PlanModel.id == UserSubscriptionModel.plan_id)\
         .filter(UserSubscriptionModel.status == SubscriptionStatusEnum.active)\
         .group_by(PlanModel.display_name).all()

        plan_distribution = [{"plan_name": r.display_name, "count": r.count} for r in plan_counts]

        return {
            "total_users": total_users,
            "plan_distribution": plan_distribution
        }
    except Exception as e:
        logger.error(f"Error in get_user_management_stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users", response_model=PaginatedResponse[UserManagementListItem],openapi_extra={"security":[{"BearerAuth":[]}]})
def list_users_managed(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1),
    search: Optional[str] = None,
    plan_id: Optional[int] = Query(None),
    sort_order: str = Query("desc", enum=["asc", "desc"])
):
    """
    Paginated, searchable, and filtered user listing for admin.
    Default sorted by last login.
    """
    try:
        # Subqueries for counts
        agent_subquery = db.session.query(
            AgentModel.user_id,
            func.count(AgentModel.id).label("agent_count")
        ).group_by(AgentModel.user_id).subquery()

        phone_subquery = db.session.query(
            PhoneNumberService.user_id,
            func.count(PhoneNumberService.id).label("phone_count")
        ).group_by(PhoneNumberService.user_id).subquery()

        coins_subquery = db.session.query(
            CoinsLedgerModel.user_id,
            func.sum(CoinsLedgerModel.remaining_coins).label("balance")
        ).group_by(CoinsLedgerModel.user_id).subquery()

        last_active_subquery = db.session.query(
            ActivityLogModel.user_id,
            func.max(ActivityLogModel.created_at).label("last_active")
        ).group_by(ActivityLogModel.user_id).subquery()

        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)
        week_ago  = now - timedelta(days=7)

        calls_total_subquery = db.session.query(
            APICallLogModel.user_id,
            func.count(APICallLogModel.id).label("calls_total")
        ).group_by(APICallLogModel.user_id).subquery()

        calls_monthly_subquery = db.session.query(
            APICallLogModel.user_id,
            func.count(APICallLogModel.id).label("calls_monthly")
        ).filter(APICallLogModel.created_at >= month_ago)\
         .group_by(APICallLogModel.user_id).subquery()

        calls_weekly_subquery = db.session.query(
            APICallLogModel.user_id,
            func.count(APICallLogModel.id).label("calls_weekly")
        ).filter(APICallLogModel.created_at >= week_ago)\
         .group_by(APICallLogModel.user_id).subquery()

        # Main query
        query = db.session.query(
            UnifiedAuthModel.id.label("user_id"),
            UnifiedAuthModel.name.label("username"),
            UnifiedAuthModel.email,
            UnifiedAuthModel.is_suspended,
            PlanModel.display_name.label("plan_name"),
            PlanModel.id.label("plan_id"),
            func.coalesce(coins_subquery.c.balance, 0).label("balance_coins"),
            func.coalesce(agent_subquery.c.agent_count, 0).label("no_of_agents"),
            func.coalesce(phone_subquery.c.phone_count, 0).label("no_of_phones"),
            func.greatest(
                UnifiedAuthModel.last_login,
                last_active_subquery.c.last_active
            ).label("last_active"),
            func.coalesce(calls_total_subquery.c.calls_total, 0).label("calls_total"),
            func.coalesce(calls_monthly_subquery.c.calls_monthly, 0).label("calls_monthly"),
            func.coalesce(calls_weekly_subquery.c.calls_weekly, 0).label("calls_weekly"),
        ).filter(UnifiedAuthModel.is_admin.is_(False))\
         .outerjoin(UserSubscriptionModel, (UnifiedAuthModel.id == UserSubscriptionModel.user_id) & (UserSubscriptionModel.status == SubscriptionStatusEnum.active))\
         .outerjoin(PlanModel, UserSubscriptionModel.plan_id == PlanModel.id)\
         .outerjoin(agent_subquery, UnifiedAuthModel.id == agent_subquery.c.user_id)\
         .outerjoin(phone_subquery, UnifiedAuthModel.id == phone_subquery.c.user_id)\
         .outerjoin(coins_subquery, UnifiedAuthModel.id == coins_subquery.c.user_id)\
         .outerjoin(last_active_subquery, UnifiedAuthModel.id == last_active_subquery.c.user_id)\
         .outerjoin(calls_total_subquery, UnifiedAuthModel.id == calls_total_subquery.c.user_id)\
         .outerjoin(calls_monthly_subquery, UnifiedAuthModel.id == calls_monthly_subquery.c.user_id)\
         .outerjoin(calls_weekly_subquery, UnifiedAuthModel.id == calls_weekly_subquery.c.user_id)

        # Search
        if search:
            query = query.filter(
                or_(
                    UnifiedAuthModel.name.ilike(f"%{search}%"),
                    UnifiedAuthModel.email.ilike(f"%{search}%")
                )
            )

        # Plan Filter
        if plan_id:
            query = query.filter(PlanModel.id == plan_id)

        # Default Sorting (Last Active)
        order_attr = last_active_subquery.c.last_active
        if sort_order == "desc":
            query = query.order_by(desc(order_attr))
        else:
            query = query.order_by(order_attr)

        # Pagination
        total_count = query.count()
        offset = (page - 1) * limit
        results = query.offset(offset).limit(limit).all()

        items = [
            UserManagementListItem(
                user_id=r.user_id,
                username=r.username or "Unknown",
                email=r.email or "",
                plan_name=r.plan_name,
                plan_id=r.plan_id,
                balance_coins=int(r.balance_coins),
                no_of_agents=r.no_of_agents,
                no_of_phones=r.no_of_phones,
                last_active=format_time_ago(r.last_active) if r.last_active else "long time ago",
                is_suspended=r.is_suspended,
                api_calls_total=r.calls_total,
                api_calls_monthly=r.calls_monthly,
                api_calls_weekly=r.calls_weekly,
            ) for r in results
        ]

        total_pages = (total_count + limit - 1) // limit if limit > 0 else 0

        return PaginatedResponse(
            total=total_count,
            page=page,
            size=limit,
            pages=total_pages,
            items=items
        )

    except Exception as e:
        logger.error(f"Error listing users managed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/users/{user_id}/suspend",openapi_extra={"security":[{"BearerAuth":[]}]})
def suspend_user(user_id:int,request:SuspendUserRequest):
    try:
        user= (db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.id == user_id,
            UnifiedAuthModel.is_admin.is_(False)
        ).first())
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail= "user not found"
            )
        user.is_suspended = request.is_suspended
        if request.is_suspended:
            if request.reason:
                user.suspension_reason = request.reason
            #disable agents for the user and pause subscription.
            web_agents = user.web_agents
            for agent in web_agents:
                agent.is_enabled = False
            #pause subscription for user
            subscriptions= user.subscriptions
            for subscription in subscriptions:
                if subscription.status == SubscriptionStatusEnum.active:
                    provider = subscription.provider
                    subscription_provider =PaymentProviderFactory.get_provider(provider)
                    #pause subscription
                    subscription_provider.pause_subscription(subscription.provider_subscription_id)
                    logger.info(f"Subscription paused for user {user_id}")
        else:
            user.suspension_reason = None
            #resume subscription for user
            subscriptions= user.subscriptions
            for subscription in subscriptions:
                if subscription.status == SubscriptionStatusEnum.paused:
                    provider = subscription.provider
                    subscription_provider =PaymentProviderFactory.get_provider(provider)
                    #resume subscription
                    subscription_provider.resume_subscription(subscription.provider_subscription_id)
                    logger.info(f"Subscription resumed for user {user_id}")
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)
        return {"message":f"User {'suspended' if request.is_suspended else 'unsuspend'} successfully"}
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error suspending user: {str(e)}")
        raise HTTPException(status_code=500,detail=str(e))

@router.post("/users/{user_id}/adjust-coins", openapi_extra={"security": [{"BearerAuth": []}]})
def adjust_user_coins(user_id: int, request: AdjustUserCoinRequest):
    """
    Adjust user coins (add or deduct) by admin.
    Positive amount adds coins, negative amount deducts coins.
    """
    try:
        user = (db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.id == user_id,
            UnifiedAuthModel.is_admin.is_(False)
        ).first())
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        success = admin_adjust_coins(
            user_id=user_id,
            amount=request.coins,
            reason=request.reason
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to adjust coins. Check if user has sufficient balance for deduction."
            )
            
        return {"message": "Coins adjusted successfully", "new_balance": get_user_coin_balance(user_id)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adjusting coins for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

