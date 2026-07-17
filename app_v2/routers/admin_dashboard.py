from fastapi import APIRouter, HTTPException, status, Depends, Query
from app_v2.utils.jwt_utils import is_admin,HTTPBearer
from datetime import datetime, date
from typing import List, Literal, Optional
from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel, ConversationsModel, CoinUsageSettingsModel, CoinUsageSettingsVersionModel, PaymentModel, PaymentStatusEnum, CoinsLedgerModel, CoinTransactionTypeEnum, APICallLogModel, APIKeyModel
from app_v2.schemas.activity_schema import ActivityLogResponse
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
)
from app_v2.schemas.enum_types import CallStatusEnum, PublicLogChannelEnum
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.core.logger import setup_logger
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_, case
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
    profit_type: Optional[Literal["profit", "loss"]] = Query(None, description="Filter to calls where profit_percentage >= 0 (profit) or < 0 (loss)"),
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
        rows = (
            db.session.query(
                APICallLogModel.channel,
                APICallLogModel.api_route,
                APICallLogModel.method,
                func.sum(case((APICallLogModel.is_success == True, 1), else_=0)).label("success_count"),
                func.sum(case((APICallLogModel.is_success == False, 1), else_=0)).label("failure_count"),
                func.count(APICallLogModel.id).label("total_count"),
            )
            .filter(APICallLogModel.channel.in_(ADMIN_PUBLIC_LOG_CHANNELS))
            .group_by(APICallLogModel.channel, APICallLogModel.api_route, APICallLogModel.method)
            .all()
        )
        endpoints = [
            AdminPublicLogEndpointItem(
                channel=row.channel.value if row.channel else PublicLogChannelEnum.public_api.value,
                route=row.api_route,
                method=row.method,
                success_count=int(row.success_count or 0),
                failure_count=int(row.failure_count or 0),
                total_count=int(row.total_count or 0),
            )
            for row in rows
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
    size: int = Query(20, ge=1, le=100),
    only_failures: bool = True,
):
    """Paginated call log rows (full request/response detail) for one endpoint, across every user."""
    try:
        from math import ceil

        skip = (page - 1) * size
        q = (
            db.session.query(APICallLogModel, UnifiedAuthModel)
            .join(UnifiedAuthModel, APICallLogModel.user_id == UnifiedAuthModel.id)
            .filter(APICallLogModel.channel == channel, APICallLogModel.api_route == route)
        )
        if only_failures:
            q = q.filter(APICallLogModel.is_success == False)

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

