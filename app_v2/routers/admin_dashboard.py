from fastapi import APIRouter, HTTPException, status, Depends, Query
from app_v2.utils.jwt_utils import is_admin,HTTPBearer
from datetime import datetime, date
from typing import List, Literal, Optional
from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel, ConversationsModel, CoinUsageSettingsModel, CoinUsageSettingsVersionModel, PaymentModel, PaymentStatusEnum, PaymentTypeEnum, CoinsLedgerModel, CoinTransactionTypeEnum, APICallLogModel, APIKeyModel, WebhookEventLogModel, AddOnCoinOrderModel
from app_v2.schemas.activity_schema import ActivityLogResponse, AdminActivityItem
from app_v2.schemas.admin_dashboard import (
    UserCostItem,
    AdminConversationItem,
    ConversationSettingsSnapshot,
    MonthlyProfitLossItem,
    OverallProfitLossSummary,
    ProfitLossAnalyticsResponse,
    AdminPublicLogEndpointItem,
    AdminPublicLogEndpointListResponse,
    AdminPublicLogItem,
    AdminPublicLogUserItem,
    AdminPublicLogUserListResponse,
    AdminWebhookEventItem,
    AdminPaymentItem,
)
from app_v2.schemas.enum_types import CallStatusEnum, PublicLogChannelEnum, ChannelEnum
from app_v2.schemas.pagination import PaginatedResponse, PageSize
from app_v2.core.logger import setup_logger
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_, case, desc, Integer
from app_v2.utils.activity_logger import get_agent_ids_matching_name, enrich_activities_with_agent_and_coins
from app_v2.utils.time_utils import format_time_ago
from app_v2.utils.analytics_utils import calculate_percentage_change, get_current_and_previous_month_start
from app_v2.utils.public_call_success_counter import get_success_counts_by_endpoint_admin
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
            detail="Failed to fetch usage and billing information."
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
    page_size: PageSize = 10,
    search: Optional[str] = Query(None, description="Search by user name/email or agent name"),
    date_after: Optional[date] = Query(None),
    date_before: Optional[date] = Query(None),
    user_id: Optional[int] = Query(None, description="Filter to a single user's conversations"),
    agent_id: Optional[int] = Query(None, description="Filter to a single agent's conversations"),
    call_status: Optional[CallStatusEnum] = Query(None, description="Filter by call status (success/failed/in_progress)"),
    profit_type: Optional[Literal["profit", "loss"]] = Query(None, description="Filter to calls where profit_percentage >= 0 (profit) or < 0 (loss)"),
    channel: Optional[ChannelEnum] = Query(None, description="Filter by conversation channel (e.g. 'api' for the public WS API, 'widget', 'web_agent', 'call', 'test_voice')"),
    cost_profit_type: Optional[Literal["llm_profit", "llm_loss", "conversation_profit", "conversation_loss"]] = Query(
        None,
        description=(
            "Filter by per-category cost variance (actual EL charge vs our calculated "
            "estimate), same convention as the Conv Δ%/LLM Δ% columns: "
            "'llm_profit'/'conversation_profit' = actual <= calculated (we didn't undercharge), "
            "'llm_loss'/'conversation_loss' = actual > calculated (we undercharged)."
        ),
    ),
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
        if profit_type == "profit":
            q = q.filter(ConversationsModel.profit_percentage >= 0)
        elif profit_type == "loss":
            q = q.filter(ConversationsModel.profit_percentage < 0)
        if channel:
            q = q.filter(ConversationsModel.channel == channel)
        if cost_profit_type == "llm_profit":
            q = q.filter(ConversationsModel.actual_llm_credits <= ConversationsModel.calculated_llm_cost)
        elif cost_profit_type == "llm_loss":
            q = q.filter(ConversationsModel.actual_llm_credits > ConversationsModel.calculated_llm_cost)
        elif cost_profit_type == "conversation_profit":
            q = q.filter(ConversationsModel.actual_conversation_credits <= ConversationsModel.calculated_conversation_cost)
        elif cost_profit_type == "conversation_loss":
            q = q.filter(ConversationsModel.actual_conversation_credits > ConversationsModel.calculated_conversation_cost)

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

        version_ids = {conv.settings_version_id for conv, _, _ in rows if conv.settings_version_id}
        if version_ids:
            versions = (
                db.session.query(CoinUsageSettingsVersionModel)
                .filter(CoinUsageSettingsVersionModel.id.in_(version_ids))
                .all()
            )
            versions_map = {v.id: ConversationSettingsSnapshot.model_validate(v) for v in versions}
        else:
            versions_map = {}

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
                user_message_count=conv.user_message_count,
                agent_message_count=conv.agent_message_count,
                system_prompt_length=conv.system_prompt_length,
                system_prompt_tokens=conv.system_prompt_tokens,
                tool_count=conv.tool_count,
                kb_total_pages=conv.kb_total_pages,
                rag_enabled=conv.rag_enabled,
                settings_version=versions_map.get(conv.settings_version_id),
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


@router.get(
    "/activities",
    response_model=PaginatedResponse[AdminActivityItem],
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_all_activities_for_admin(
    page: int = Query(1, ge=1),
    page_size: PageSize = 10,
    date_after: Optional[date] = Query(None),
    date_before: Optional[date] = Query(None),
    event_type: Optional[str] = Query(None, description="Filter by activity/event type (partial match)"),
    user_email: Optional[str] = Query(None, description="Filter by the acting user's email (partial match)"),
    agent_name: Optional[str] = Query(None, description="Filter by agent name (partial match)"),
):
    """
    Every user's activity log entries side by side, admin-only equivalent of
    the user-side /user-dashboard/activities feed — with the acting user's
    email attached to each row and filters for date range/event type/email/agent.
    """
    try:
        q = (
            db.session.query(ActivityLogModel, UnifiedAuthModel)
            .join(UnifiedAuthModel, ActivityLogModel.user_id == UnifiedAuthModel.id)
        )

        if date_after:
            q = q.filter(ActivityLogModel.created_at >= date_after)
        if date_before:
            q = q.filter(ActivityLogModel.created_at <= date_before)
        if event_type:
            q = q.filter(ActivityLogModel.event_type.ilike(f"%{event_type}%"))
        if user_email:
            q = q.filter(UnifiedAuthModel.email.ilike(f"%{user_email}%"))
        if agent_name:
            matching_agent_ids = get_agent_ids_matching_name(agent_name)
            if not matching_agent_ids:
                return PaginatedResponse(total=0, page=page, size=page_size, pages=1, items=[])
            q = q.filter(
                ActivityLogModel.metadata_json["agent_id"].astext.cast(Integer).in_(matching_agent_ids)
            )

        q = q.order_by(ActivityLogModel.created_at.desc())

        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        enrichment = enrich_activities_with_agent_and_coins([log for log, _ in rows])

        items = [
            AdminActivityItem(
                id=log.id,
                user_id=log.user_id,
                user_name=user.name or user.username or "Unknown",
                user_email=user.email or "",
                agent_name=enrichment[log.id]["agent_name"],
                coins=enrichment[log.id]["coins"],
                event_type=log.event_type,
                description=log.description,
                metadata_json=log.metadata_json,
                created_at=log.created_at,
                time_ago=format_time_ago(log.created_at),
            )
            for log, user in rows
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
        logger.error(f"Error in list_all_activities_for_admin: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch activities: {str(e)}"
        )


@router.get(
    "/profit-loss/monthly",
    response_model=ProfitLossAnalyticsResponse,
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def get_monthly_profit_loss():
    """
    Month-by-month split of every conversation with a computed
    profit_percentage into "profit" (>= 0) and "loss" (< 0) buckets, plus an
    overall summary across all months. Calls with no profit_percentage yet
    (e.g. never finalized) are excluded entirely.

    - profit_pct_share / loss_pct_share: what fraction of that month's
      classified calls were profitable vs a loss (sums to ~100%).
    - avg_profit_percentage / avg_loss_percentage: the mean profit_percentage
      magnitude within each bucket — a different, complementary number from
      the share above (e.g. a month can have a high profit SHARE but a small
      profit MAGNITUDE per call, or vice versa).
    """
    try:
        month_expr = func.date_trunc('month', ConversationsModel.created_at)
        is_profit = ConversationsModel.profit_percentage >= 0
        is_loss = ConversationsModel.profit_percentage < 0

        rows = (
            db.session.query(
                month_expr.label("month"),
                func.count(case((is_profit, 1))).label("profit_count"),
                func.count(case((is_loss, 1))).label("loss_count"),
                func.avg(case((is_profit, ConversationsModel.profit_percentage))).label("avg_profit_pct"),
                func.avg(case((is_loss, ConversationsModel.profit_percentage))).label("avg_loss_pct"),
            )
            .filter(ConversationsModel.profit_percentage.isnot(None))
            .group_by(month_expr)
            .order_by(month_expr.desc())
            .all()
        )

        months = []
        for r in rows:
            profit_count = r.profit_count or 0
            loss_count = r.loss_count or 0
            total_classified = profit_count + loss_count
            months.append(MonthlyProfitLossItem(
                month=r.month.strftime("%Y-%m"),
                total_calls=total_classified,
                profit_call_count=profit_count,
                loss_call_count=loss_count,
                profit_pct_share=round(profit_count / total_classified * 100, 2) if total_classified else 0.0,
                loss_pct_share=round(loss_count / total_classified * 100, 2) if total_classified else 0.0,
                avg_profit_percentage=round(r.avg_profit_pct, 2) if r.avg_profit_pct is not None else None,
                avg_loss_percentage=round(r.avg_loss_pct, 2) if r.avg_loss_pct is not None else None,
            ))

        overall_row = (
            db.session.query(
                func.count(case((is_profit, 1))).label("profit_count"),
                func.count(case((is_loss, 1))).label("loss_count"),
                func.avg(case((is_profit, ConversationsModel.profit_percentage))).label("avg_profit_pct"),
                func.avg(case((is_loss, ConversationsModel.profit_percentage))).label("avg_loss_pct"),
            )
            .filter(ConversationsModel.profit_percentage.isnot(None))
            .first()
        )
        overall_profit_count = (overall_row.profit_count or 0) if overall_row else 0
        overall_loss_count = (overall_row.loss_count or 0) if overall_row else 0
        overall_total = overall_profit_count + overall_loss_count
        months_count = len(months) or 1

        overall = OverallProfitLossSummary(
            total_calls=overall_total,
            profit_call_count=overall_profit_count,
            loss_call_count=overall_loss_count,
            profit_pct_share=round(overall_profit_count / overall_total * 100, 2) if overall_total else 0.0,
            loss_pct_share=round(overall_loss_count / overall_total * 100, 2) if overall_total else 0.0,
            avg_profit_percentage=round(overall_row.avg_profit_pct, 2) if overall_row and overall_row.avg_profit_pct is not None else None,
            avg_loss_percentage=round(overall_row.avg_loss_pct, 2) if overall_row and overall_row.avg_loss_pct is not None else None,
            months_count=len(months),
            avg_profit_call_count_per_month=round(overall_profit_count / months_count, 2),
            avg_loss_call_count_per_month=round(overall_loss_count / months_count, 2),
        )

        return ProfitLossAnalyticsResponse(months=months, overall=overall)
    except Exception as e:
        logger.error(f"Error in get_monthly_profit_loss: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch monthly profit/loss: {str(e)}"
        )


# ---- Public API / Public Websocket Logs dashboard (admin-wide, all users) ----

ADMIN_PUBLIC_LOG_CHANNELS = [
    PublicLogChannelEnum.public_api,
    PublicLogChannelEnum.public_websocket,
    PublicLogChannelEnum.widget_websocket,
]


@router.get(
    "/public-logs/endpoints",
    response_model=AdminPublicLogEndpointListResponse,
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_public_log_endpoints_for_admin():
    """All-time success/failure/total counts per (channel, route, method), across every user."""
    try:
        failure_rows = (
            db.session.query(
                APICallLogModel.channel,
                APICallLogModel.api_route,
                APICallLogModel.method,
                func.count(APICallLogModel.id).label("failure_count"),
            )
            .filter(
                APICallLogModel.channel.in_(ADMIN_PUBLIC_LOG_CHANNELS),
                APICallLogModel.is_success == False,
            )
            .group_by(APICallLogModel.channel, APICallLogModel.api_route, APICallLogModel.method)
            .all()
        )
        failure_map = {
            (
                row.channel.value if row.channel else PublicLogChannelEnum.public_api.value,
                row.api_route,
                (row.method or "UNKNOWN").upper(),
            ): int(row.failure_count or 0)
            for row in failure_rows
        }
        success_map = get_success_counts_by_endpoint_admin(ADMIN_PUBLIC_LOG_CHANNELS)

        endpoints = [
            AdminPublicLogEndpointItem(
                channel=key[0],
                route=key[1],
                method=None if key[2] == "UNKNOWN" else key[2],
                success_count=success_map.get(key, 0),
                failure_count=failure_map.get(key, 0),
                total_count=success_map.get(key, 0) + failure_map.get(key, 0),
            )
            for key in set(failure_map.keys()) | set(success_map.keys())
        ]
        return AdminPublicLogEndpointListResponse(endpoints=endpoints)
    except Exception as e:
        logger.error(f"Error in list_public_log_endpoints_for_admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/public-logs/logs",
    response_model=PaginatedResponse[AdminPublicLogItem],
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_public_logs_for_admin(
    channel: PublicLogChannelEnum,
    route: str,
    page: int = Query(1, ge=1),
    size: PageSize = 10,
):
    """Paginated call log rows (full request/response detail) for one endpoint, across every user.

    Always scoped to failures: successful calls' bodies are never persisted,
    so there's nothing to click through to for a success count.
    """
    try:
        from math import ceil

        skip = (page - 1) * size
        q = (
            db.session.query(APICallLogModel, UnifiedAuthModel)
            .join(UnifiedAuthModel, APICallLogModel.user_id == UnifiedAuthModel.id)
            .filter(
                APICallLogModel.channel == channel,
                APICallLogModel.api_route == route,
                APICallLogModel.is_success == False,
            )
        )

        total = q.count()
        rows = q.order_by(APICallLogModel.created_at.desc()).offset(skip).limit(size).all()
        total_pages = ceil(total / size) if size > 0 else 1

        key_ids = {log.api_key_id for log, _ in rows if log.api_key_id}
        key_names = {}
        if key_ids:
            key_names = {
                k.id: k.name or k.client_id
                for k in db.session.query(APIKeyModel).filter(APIKeyModel.id.in_(key_ids)).all()
            }

        items = [
            AdminPublicLogItem(
                id=log.id,
                channel=log.channel.value if log.channel else None,
                api_route=log.api_route,
                method=log.method,
                status_code=log.status_code,
                is_success=log.is_success,
                request_params=log.request_params,
                request_body=log.request_body,
                response_body=log.response_body,
                error_message=log.error_message,
                response_time_ms=log.response_time_ms,
                created_at=log.created_at,
                api_key_id=log.api_key_id,
                api_key_name=key_names.get(log.api_key_id),
                user_id=user.id,
                user_name=user.name,
                user_email=user.email,
            )
            for log, user in rows
        ]
        return PaginatedResponse[AdminPublicLogItem](total=total, page=page, size=size, pages=total_pages, items=items)
    except Exception as e:
        logger.error(f"Error in list_public_logs_for_admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/webhook-events",
    response_model=PaginatedResponse[AdminWebhookEventItem],
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_webhook_events(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    provider: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    event_type: Optional[str] = None,
    payment_id: Optional[str] = None,
    order_id: Optional[str] = None,
    user_id: Optional[int] = None,
    email: Optional[str] = None,
):
    """
    Paginated inbound webhook delivery log (Razorpay et al), newest first.

    payment_id/order_id are extracted from the raw JSONB payload (Razorpay
    doesn't give us dedicated columns for these), and user_id/email are
    resolved by joining that extracted order_id against the addon order it
    belongs to — so a webhook event can be traced back to the user/payment
    it's about even though WebhookEventLogModel itself has no direct FK.
    """
    try:
        from math import ceil

        payment_id_col = func.jsonb_extract_path_text(
            WebhookEventLogModel.payload, "payload", "payment", "entity", "id"
        )
        order_id_col = func.coalesce(
            func.jsonb_extract_path_text(
                WebhookEventLogModel.payload, "payload", "payment", "entity", "order_id"
            ),
            func.jsonb_extract_path_text(
                WebhookEventLogModel.payload, "payload", "order", "entity", "id"
            ),
        )

        q = (
            db.session.query(
                WebhookEventLogModel,
                payment_id_col,
                order_id_col,
                AddOnCoinOrderModel.user_id,
                UnifiedAuthModel.email,
            )
            .outerjoin(AddOnCoinOrderModel, AddOnCoinOrderModel.provider_order_id == order_id_col)
            .outerjoin(UnifiedAuthModel, UnifiedAuthModel.id == AddOnCoinOrderModel.user_id)
        )
        if provider:
            q = q.filter(WebhookEventLogModel.provider == provider)
        if status_filter:
            q = q.filter(WebhookEventLogModel.status == status_filter)
        if event_type:
            q = q.filter(WebhookEventLogModel.event_type == event_type)
        if payment_id:
            q = q.filter(payment_id_col == payment_id)
        if order_id:
            q = q.filter(order_id_col == order_id)
        if user_id:
            q = q.filter(AddOnCoinOrderModel.user_id == user_id)
        if email:
            q = q.filter(UnifiedAuthModel.email.ilike(f"%{email}%"))

        total = q.count()
        skip = (page - 1) * size
        rows = (
            q.order_by(WebhookEventLogModel.created_at.desc())
            .offset(skip)
            .limit(size)
            .all()
        )
        total_pages = ceil(total / size) if size > 0 else 1

        items = [
            AdminWebhookEventItem(
                id=log.id,
                provider=log.provider,
                event_id=log.event_id,
                event_type=log.event_type,
                status=log.status,
                error_message=log.error_message,
                created_at=log.created_at,
                processed_at=log.processed_at,
                payment_id=resolved_payment_id,
                order_id=resolved_order_id,
                user_id=resolved_user_id,
                user_email=resolved_email,
                payload=log.payload,
            )
            for log, resolved_payment_id, resolved_order_id, resolved_user_id, resolved_email in rows
        ]
        return PaginatedResponse[AdminWebhookEventItem](total=total, page=page, size=size, pages=total_pages, items=items)
    except Exception as e:
        logger.error(f"Error in list_webhook_events: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def _describe_payment(p: PaymentModel) -> str:
    if p.payment_type in (PaymentTypeEnum.coin_purchase, PaymentTypeEnum.addon):
        coins = p.metadata_json.get("coins") if p.metadata_json else None
        return f"Credit Purchase ({coins} credits)" if coins else "Credit Purchase"
    elif p.payment_type == PaymentTypeEnum.subscription:
        return "Subscription Payment"
    return "Miscellaneous Payment"


def _describe_admin_adjustment(coins: int) -> str:
    sign = "+" if coins > 0 else ""
    return f"Admin Adjustment ({sign}{coins} coins)"


@router.get(
    "/payments",
    response_model=PaginatedResponse[AdminPaymentItem],
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_all_payments_for_admin(
    page: int = Query(1, ge=1),
    size: PageSize = 10,
    status_filter: Optional[str] = Query(None, alias="status"),
    email: Optional[str] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    date_after: Optional[str] = None,
    date_before: Optional[str] = None,
    entry_type: Optional[str] = Query(None, enum=["payment", "admin_adjustment"]),
    sort_by: str = Query("payment_date", enum=["payment_date", "amount", "user_id"]),
    sort_order: str = Query("desc", enum=["asc", "desc"]),
):
    """
    Every credit-affecting transaction across every user, newest first by
    default — both real payment transactions (PaymentModel) AND manual admin
    coin adjustments (CoinsLedgerModel), merged into one feed so an admin
    doesn't have to check two different places for "where did this user's
    coins come from".

    entry_type filters to just one source ("payment" = credits purchased,
    "admin_adjustment" = credits added/removed by an admin). status/amount
    range are payment-only concepts — setting either excludes admin
    adjustments from the merged results entirely, since a ledger adjustment
    has no processing status and its "amount" (coins) isn't the same unit as
    a payment's currency amount.
    """
    try:
        combined: List[AdminPaymentItem] = []

        if entry_type != "admin_adjustment":
            pq = (
                db.session.query(PaymentModel, UnifiedAuthModel.email)
                .join(UnifiedAuthModel, PaymentModel.user_id == UnifiedAuthModel.id)
            )
            if status_filter:
                pq = pq.filter(PaymentModel.status == status_filter)
            if email:
                pq = pq.filter(UnifiedAuthModel.email.ilike(f"%{email}%"))
            if amount_min is not None:
                pq = pq.filter(PaymentModel.amount >= amount_min)
            if amount_max is not None:
                pq = pq.filter(PaymentModel.amount <= amount_max)
            if date_after:
                pq = pq.filter(PaymentModel.created_at >= date_after)
            if date_before:
                pq = pq.filter(PaymentModel.created_at <= date_before)

            for p, user_email in pq.all():
                combined.append(AdminPaymentItem(
                    entry_type="payment",
                    payment_id=p.id,
                    user_id=p.user_id,
                    user_email=user_email,
                    date=p.created_at,
                    description=_describe_payment(p),
                    amount=p.amount,
                    currency=p.currency,
                    status=p.status.value if hasattr(p.status, "value") else p.status,
                    provider=p.provider.value if hasattr(p.provider, "value") else p.provider,
                    provider_payment_id=p.provider_payment_id,
                    provider_order_id=p.provider_order_id,
                ))

        if entry_type != "payment" and not status_filter and amount_min is None and amount_max is None:
            aq = (
                db.session.query(CoinsLedgerModel, UnifiedAuthModel.email)
                .join(UnifiedAuthModel, CoinsLedgerModel.user_id == UnifiedAuthModel.id)
                .filter(CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.admin_adjustment)
            )
            if email:
                aq = aq.filter(UnifiedAuthModel.email.ilike(f"%{email}%"))
            if date_after:
                aq = aq.filter(CoinsLedgerModel.created_at >= date_after)
            if date_before:
                aq = aq.filter(CoinsLedgerModel.created_at <= date_before)

            for entry, user_email in aq.all():
                combined.append(AdminPaymentItem(
                    entry_type="admin_adjustment",
                    payment_id=entry.id,
                    user_id=entry.user_id,
                    user_email=user_email,
                    date=entry.created_at,
                    description=_describe_admin_adjustment(entry.coins),
                    coins=entry.coins,
                    reason=entry.notes,
                ))

        def sort_key(item: AdminPaymentItem):
            if sort_by == "amount":
                return item.amount if item.amount is not None else (item.coins or 0)
            if sort_by == "user_id":
                return item.user_id
            return item.date

        combined.sort(key=sort_key, reverse=(sort_order == "desc"))

        total = len(combined)
        start = (page - 1) * size
        items = combined[start:start + size]
        total_pages = (total + size - 1) // size if size > 0 else 0

        return PaginatedResponse(total=total, page=page, size=size, pages=total_pages, items=items)
    except Exception as e:
        logger.error(f"Error in list_all_payments_for_admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/payments/{payment_id}/invoice",
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def get_admin_payment_invoice(payment_id: int):
    """
    Resolves to the plain, directly-navigable invoice PDF URL for any payment
    regardless of which user it belongs to (global admin lookup — the
    per-user equivalent is admin_user_management.get_user_billing_invoice).
    """
    payment = db.session.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return {"path": f"/invoices/{payment.invoice_reference}.pdf"}


@router.get(
    "/public-logs/users",
    response_model=AdminPublicLogUserListResponse,
    dependencies=[Depends(is_admin)],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_public_log_users_for_admin(channel: PublicLogChannelEnum, route: str):
    """Users who had at least one failure on this endpoint, with their failure/total counts."""
    try:
        failure_case = case((APICallLogModel.is_success == False, 1), else_=0)
        rows = (
            db.session.query(
                UnifiedAuthModel.id.label("user_id"),
                UnifiedAuthModel.name.label("user_name"),
                UnifiedAuthModel.email.label("user_email"),
                func.sum(failure_case).label("failure_count"),
                func.count(APICallLogModel.id).label("total_count"),
            )
            .join(APICallLogModel, APICallLogModel.user_id == UnifiedAuthModel.id)
            .filter(APICallLogModel.channel == channel, APICallLogModel.api_route == route)
            .group_by(UnifiedAuthModel.id, UnifiedAuthModel.name, UnifiedAuthModel.email)
            .having(func.sum(failure_case) > 0)
            .order_by(func.sum(failure_case).desc())
            .all()
        )
        users = [
            AdminPublicLogUserItem(
                user_id=row.user_id,
                user_name=row.user_name,
                user_email=row.user_email,
                failure_count=int(row.failure_count or 0),
                total_count=int(row.total_count or 0),
            )
            for row in rows
        ]
        return AdminPublicLogUserListResponse(users=users)
    except Exception as e:
        logger.error(f"Error in list_public_log_users_for_admin: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

