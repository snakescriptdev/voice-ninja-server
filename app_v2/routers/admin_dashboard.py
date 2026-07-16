from fastapi import APIRouter, HTTPException, status, Depends, Query
from app_v2.utils.jwt_utils import is_admin,HTTPBearer
from datetime import datetime, date
from typing import List, Literal, Optional
from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel, ConversationsModel, CoinUsageSettingsModel, PaymentModel, PaymentStatusEnum, CoinsLedgerModel, CoinTransactionTypeEnum, APICallLogModel
from app_v2.schemas.activity_schema import ActivityLogResponse
from app_v2.schemas.admin_dashboard import UserCostItem, AdminConversationItem
from app_v2.schemas.enum_types import CallStatusEnum
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.core.logger import setup_logger
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_
from app_v2.utils.time_utils import format_time_ago
from app_v2.utils.analytics_utils import calculate_percentage_change, get_current_and_previous_month_start
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
        ).outerjoin(cost_query, UnifiedAuthModel.id == cost_query.c.user_id
        ).filter(UnifiedAuthModel.is_admin == False)

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


@router.get(
    "/elevenlabs/conversations",
    response_model=PaginatedResponse[AdminConversationItem],
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_all_conversations_for_admin(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by user name/email or agent name"),
    date_after: Optional[date] = Query(None),
    date_before: Optional[date] = Query(None),
    user_id: Optional[int] = Query(None, description="Filter to a single user's conversations"),
    agent_id: Optional[int] = Query(None, description="Filter to a single agent's conversations"),
    call_status: Optional[CallStatusEnum] = Query(None, description="Filter by call status (success/failed/in_progress)"),
):
    """
    Every conversation across every user, side by side with the raw
    ElevenLabs cost we were charged (`ConversationsModel.cost`) and the coins
    we actually deducted from the user for it (from `CoinsLedgerModel`) — so
    admins can audit that the two line up as expected.
    """
    try:
        q = (
            db.session.query(ConversationsModel, UnifiedAuthModel, AgentModel)
            .join(UnifiedAuthModel, ConversationsModel.user_id == UnifiedAuthModel.id)
            .outerjoin(AgentModel, ConversationsModel.agent_id == AgentModel.id)
        )

        if search:
            q = q.filter(
                or_(
                    UnifiedAuthModel.name.ilike(f"%{search}%"),
                    UnifiedAuthModel.email.ilike(f"%{search}%"),
                    AgentModel.agent_name.ilike(f"%{search}%"),
                )
            )
        if date_after:
            q = q.filter(ConversationsModel.created_at >= date_after)
        if date_before:
            q = q.filter(ConversationsModel.created_at <= date_before)
        if user_id is not None:
            q = q.filter(ConversationsModel.user_id == user_id)
        if agent_id is not None:
            q = q.filter(ConversationsModel.agent_id == agent_id)
        if call_status:
            q = q.filter(ConversationsModel.call_status == call_status)

        q = q.order_by(ConversationsModel.created_at.desc())

        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()

        conv_ids = [conv.id for conv, _, _ in rows]
        if conv_ids:
            ledger_entries = db.session.query(
                CoinsLedgerModel.reference_id, CoinsLedgerModel.coins
            ).filter(
                CoinsLedgerModel.reference_type == "conversation",
                CoinsLedgerModel.reference_id.in_(conv_ids),
                CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.debit_usage,
            ).all()
            coins_deducted_map = {entry.reference_id: abs(entry.coins) for entry in ledger_entries}
        else:
            coins_deducted_map = {}

        items = [
            AdminConversationItem(
                id=conv.id,
                created_at=conv.created_at,
                user_id=user.id,
                user_name=user.name or user.username or "Unknown",
                user_email=user.email or "",
                agent_name=agent.agent_name if agent else None,
                elevenlabs_agent_id=agent.elevenlabs_agent_id if agent else None,
                channel=conv.channel.value if conv.channel else None,
                call_status=conv.call_status.name if conv.call_status else None,
                duration=conv.duration,
                elevenlabs_conv_id=conv.elevenlabs_conv_id,
                elevenlabs_cost=float(conv.cost or 0),
                coins_deducted=coins_deducted_map.get(conv.id, 0),
                actual_conversation_credits=conv.actual_conversation_credits,
                actual_llm_credits=conv.actual_llm_credits,
                actual_telephony_cost=0.0,
                calculated_conversation_cost=conv.calculated_conversation_cost,
                calculated_llm_cost=conv.calculated_llm_cost,
                calculated_telephony_cost=conv.calculated_telephony_cost or 0.0,
                profit_percentage=conv.profit_percentage,
            )
            for conv, user, agent in rows
        ]

        from math import ceil
        total_pages = ceil(total / page_size) if page_size > 0 else 1

        return PaginatedResponse(
            total=total,
            page=page,
            size=page_size,
            pages=total_pages,
            items=items,
        )

    except Exception as e:
        logger.error(f"Error in list_all_conversations_for_admin: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch conversations: {str(e)}"
        )

