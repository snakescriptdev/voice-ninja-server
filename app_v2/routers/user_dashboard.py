from fastapi import APIRouter, status, Depends,HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional, List
import os
from fastapi.requests import Request
from fastapi_sqlalchemy import db
from datetime import datetime, timezone, timedelta
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer
from app_v2.utils.feature_access import RequireFeature
from app_v2.databases.models import (
    UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel,
    ConversationsModel, CoinsLedgerModel,
    PaymentModel, WidgetModel, WidgetLeadModel,APIDailyUsageModel,
    APICallLogModel, APIKeyModel
)
from app_v2.utils.analytics_utils import calculate_percentage_change, get_current_and_previous_month_start
from app_v2.schemas.enum_types import (
    CoinTransactionTypeEnum, PaymentStatusEnum, PaymentTypeEnum,
    SUBSCRIPTION_BILLING_EVENT_TYPES, SUBSCRIPTION_BILLING_EVENT_STATUS_LABELS,
    PublicLogChannelEnum,
)
from app_v2.utils.coin_utils import get_user_coin_balance
from app_v2.constants import api_list

from sqlalchemy import func
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.user_dashboard import (
    UserDashboardAgentResponse,
    UserDashboardPhoneNumberResponse,
    UserAnalyticsResponse,
    HourlyDistribution,
    AgentAnalytics,
    ChannelDistribution,
    UserCoinUsageResponse,
    CoinBucketsResponse,
    CoinBucketItem,
    UsageHistoryResponse,
    UsageHistoryItem,
    BillingHistoryResponse,
    BillingHistoryItem,
    DailyTrendSeries,
    UserAPICallLogResponse,
    UserAPICallLogItem,
    PublicAPIUsageResponse,
    APIUsageDailyItem,
    APIListItem,
    DashboardLeadItem,
    DashboardLeadListResponse,
    AgentSummaryItem,
    PublicLogEndpointItem,
    PublicLogEndpointListResponse,
    DayOfMonthBucket,
    PublicLogGraphResponse,
    PublicLogItem,
    PublicLogListResponse,
    PublicLogOverviewResponse,
)
from app_v2.utils.agent_summary import build_agent_summaries
from app_v2.core.logger import setup_logger
from app_v2.utils.time_utils import format_time_ago
from math import ceil
import calendar
from sqlalchemy import case

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(prefix="/api/v2/user-dashboard", tags=["User Dashboard"], dependencies=[Depends(security)])


@router.get("/agents-data", status_code=status.HTTP_200_OK,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_agents_data(skip: int = 0, limit: int = 3, current_user: str = Depends(require_active_user())):
    try:
        count = db.session.query(AgentModel).filter(
            AgentModel.user_id == current_user.id,
            AgentModel.is_enabled.is_(True)
            ).count()
        agents = db.session.query(AgentModel).filter(
            AgentModel.user_id == current_user.id,
            AgentModel.is_enabled.is_(True)
            ).order_by(AgentModel.created_at.desc()).offset(skip).limit(limit).all()
        
        total_pages = ceil(count / limit)
        current_page = skip // limit + 1
        return PaginatedResponse(
            total=count,
            page=current_page,
            size=limit,
            pages=total_pages,
            items=[UserDashboardAgentResponse(id=agent.id, agent_name=agent.agent_name, is_enabled=agent.is_enabled, calls=len(agent.conversations)) for agent in agents]
        )
    except Exception as e:
        logger.error(f"error while fetching the agents data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to fetch the agents data at the moment:{str(e)}"
        )
        

@router.get("/phone-numbers", status_code=status.HTTP_200_OK,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_phone_numbers(skip: int = 0, limit: int = 3, current_user: str = Depends(require_active_user())):
    try:
        count = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.user_id == current_user.id
            ).count()
        phone_numbers = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.user_id == current_user.id
            ).order_by(PhoneNumberService.created_at.desc()).offset(skip).limit(limit).all()
        
        total_pages = ceil(count / limit)
        current_page = skip // limit + 1
        return PaginatedResponse(
            total=count,
            page=current_page,
            size=limit,
            pages=total_pages,
            items=[UserDashboardPhoneNumberResponse(id=phone_number.id, phone_number=phone_number.phone_number) for phone_number in phone_numbers]
        )
    except Exception as e:
        logger.error(f"error while fetching the phone numbers data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to fetch the phone numbers data at the moment:{str(e)}"
        )

@router.get("/activities", response_model=dict,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_global_activities(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(require_active_user())
):
    try:
        skip = (page - 1) * size
        
        query = db.session.query(ActivityLogModel).filter(ActivityLogModel.user_id==current_user.id).order_by(ActivityLogModel.created_at.desc())
        total = query.count()
        
        logs = query.offset(skip).limit(size).all()
        
        results = []
        for log in logs:
            results.append({
                "id": log.id,
                "user_id": log.user_id,
                "user_name": log.user.name or log.user.username or "Unknown",
                "event_type": log.event_type,
                "description": log.description,
                "metadata_json": log.metadata_json,
                "created_at": log.created_at,
                "time_ago": format_time_ago(log.created_at)
            })
            
        return {
            "status": "success",
            "total": total,
            "page": page,
            "size": size,
            "activities": results
        }
    except Exception as e:
        logger.error(f"Error in get_global_activities: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/analytics", response_model=UserAnalyticsResponse,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_analytics(current_user: UnifiedAuthModel = Depends(RequireFeature("analytics_dashboard"))):
    try:
        first_day_of_month, first_day_prev_month = get_current_and_previous_month_start()

        total_calls = db.session.query(func.count(ConversationsModel.id)).filter(
            ConversationsModel.user_id == current_user.id
        ).scalar() or 0
        
        avg_duration = (db.session.query(func.avg(ConversationsModel.duration)).filter(
            ConversationsModel.user_id == current_user.id
        ).scalar() or 0.0)/60

        curr_calls = db.session.query(func.count(ConversationsModel.id)).filter(
            ConversationsModel.user_id == current_user.id,
            ConversationsModel.created_at >= first_day_of_month
        ).scalar() or 0
        prev_calls = db.session.query(func.count(ConversationsModel.id)).filter(
            ConversationsModel.user_id == current_user.id,
            ConversationsModel.created_at >= first_day_prev_month,
            ConversationsModel.created_at < first_day_of_month
        ).scalar() or 0
        total_calls_change = calculate_percentage_change(curr_calls, prev_calls)

        curr_avg_dur = db.session.query(func.avg(ConversationsModel.duration)).filter(
            ConversationsModel.user_id == current_user.id,
            ConversationsModel.created_at >= first_day_of_month
        ).scalar() or 0.0
        prev_avg_dur = db.session.query(func.avg(ConversationsModel.duration)).filter(
            ConversationsModel.user_id == current_user.id,
            ConversationsModel.created_at >= first_day_prev_month,
            ConversationsModel.created_at < first_day_of_month
        ).scalar() or 0.0
        avg_call_duration_change = calculate_percentage_change(curr_avg_dur, prev_avg_dur)

        coin_used_this_month = db.session.query(func.abs(func.sum(CoinsLedgerModel.coins))).filter(
            CoinsLedgerModel.user_id == current_user.id,
            CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.debit_usage,
            CoinsLedgerModel.created_at >= first_day_of_month
        ).scalar() or 0
        
        coin_used_prev_month = db.session.query(func.abs(func.sum(CoinsLedgerModel.coins))).filter(
            CoinsLedgerModel.user_id == current_user.id,
            CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.debit_usage,
            CoinsLedgerModel.created_at >= first_day_prev_month,
            CoinsLedgerModel.created_at < first_day_of_month
        ).scalar() or 0
        coin_used_this_month_change = calculate_percentage_change(coin_used_this_month, coin_used_prev_month)

        active_leads_count = db.session.query(func.count(WidgetLeadModel.id)).join(
            WidgetModel, WidgetLeadModel.widget_id == WidgetModel.id
        ).filter(
            WidgetModel.user_id == current_user.id
        ).scalar() or 0
        
        curr_leads = db.session.query(func.count(WidgetLeadModel.id)).join(
            WidgetModel, WidgetLeadModel.widget_id == WidgetModel.id
        ).filter(
            WidgetModel.user_id == current_user.id,
            WidgetLeadModel.created_at >= first_day_of_month
        ).scalar() or 0
        
        prev_leads = db.session.query(func.count(WidgetLeadModel.id)).join(
            WidgetModel, WidgetLeadModel.widget_id == WidgetModel.id
        ).filter(
            WidgetModel.user_id == current_user.id,
            WidgetLeadModel.created_at >= first_day_prev_month,
            WidgetLeadModel.created_at < first_day_of_month
        ).scalar() or 0
        active_leads_count_change = calculate_percentage_change(curr_leads, prev_leads)
        
        now = datetime.now(timezone.utc)
        seven_days_ago = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)

        def get_daily_counts(model, user_id_attr, date_attr, value_attr=None, filter_type=None):
            query = db.session.query(
                func.date(date_attr).label('date'),
                (func.sum(func.abs(value_attr)) if value_attr is not None else func.count(model.id)).label('value')
            ).filter(
                user_id_attr == current_user.id,
                date_attr >= seven_days_ago
            )
            if filter_type is not None:
                query = query.filter(filter_type)
            
            return {str(r.date): float(r.value) for r in query.group_by(func.date(date_attr)).all()}

        call_daily = get_daily_counts(ConversationsModel, ConversationsModel.user_id, ConversationsModel.created_at)
        coin_daily = get_daily_counts(
            CoinsLedgerModel, 
            CoinsLedgerModel.user_id, 
            CoinsLedgerModel.created_at, 
            value_attr=CoinsLedgerModel.coins,
            filter_type=(CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.debit_usage)
        )

        call_trends = []
        coin_trends = []
        for i in range(7):
            day = (seven_days_ago + timedelta(days=i)).date()
            day_str = str(day)
            
            call_trends.append(DailyTrendSeries(
                date=day_str,
                value=call_daily.get(day_str, 0)
            ))
            coin_trends.append(DailyTrendSeries(
                date=day_str,
                value=coin_daily.get(day_str, 0)
            ))

        hourly_data = db.session.query(
            func.extract('hour', ConversationsModel.created_at).label('hour'),
            func.count(ConversationsModel.id).label('count')
        ).filter(
            ConversationsModel.user_id == current_user.id
        ).group_by('hour').all()
        
        def format_hour(h):
            h = int(h)
            if h == 0: return "12 AM"
            if h == 12: return "12 PM"
            if h < 12: return f"{h} AM"
            return f"{h-12} PM"

        hourly_list = [
            HourlyDistribution(
                hour=int(h.hour), 
                time_label=format_hour(h.hour), 
                count=h.count
            ) for h in hourly_data
        ]
        
        agent_data = db.session.query(
            AgentModel.id.label('agent_id'),
            AgentModel.agent_name,
            func.count(ConversationsModel.id).label('call_count'),
            func.avg(ConversationsModel.duration).label('avg_duration'),
            func.sum(ConversationsModel.cost).label('total_cost')
        ).join(ConversationsModel, AgentModel.id == ConversationsModel.agent_id)\
         .filter(ConversationsModel.user_id == current_user.id)\
         .group_by(AgentModel.id, AgentModel.agent_name).all()
        
        agent_list = [
            AgentAnalytics(
                agent_id=a.agent_id,
                agent_name=a.agent_name,
                call_count=a.call_count,
                avg_duration=round(float(a.avg_duration or 0), 2),
                coins_used=int(a.total_cost or 0)
            ) for a in agent_data
        ]
        
        channel_data = db.session.query(
            ConversationsModel.channel,
            func.count(ConversationsModel.id).label('count')
        ).filter(
            ConversationsModel.user_id == current_user.id
        ).group_by(ConversationsModel.channel).all()
        
        channel_list = []
        for c in channel_data:
            if c.channel is not None:
                count = c.count
                percentage = round((count / total_calls * 100), 2) if total_calls > 0 else 0.0
                channel_name = str(c.channel.value if hasattr(c.channel, 'value') else c.channel)
                channel_list.append(ChannelDistribution(
                    channel=channel_name, 
                    count=count, 
                    percentage=percentage
                ))
        
        return UserAnalyticsResponse(
            total_calls=total_calls,
            total_calls_change=float(total_calls_change),
            avg_call_duration=round(float(avg_duration), 2),
            avg_call_duration_change=float(avg_call_duration_change),
            coin_used_this_month=int(coin_used_this_month),
            coin_used_this_month_change=float(coin_used_this_month_change),
            active_leads_count=active_leads_count,
            active_leads_count_change=float(active_leads_count_change),
            hourly_distribution=hourly_list,
            agent_analytics=agent_list,
            channel_distribution=channel_list,
            call_trends=call_trends,
            coin_trends=coin_trends
        )
        
    except Exception as e:
        logger.error(f"Error in get_user_analytics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch analytics data: {str(e)}"
        )


@router.get("/coin-usage", response_model=UserCoinUsageResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_coin_usage(current_user: UnifiedAuthModel = Depends(require_active_user(allow_suspended=True))):
    try:
        balance = get_user_coin_balance(current_user.id)
        
        now = datetime.now(timezone.utc)
        first_day_of_month = datetime(now.year, now.month, 1)
        
        usage = db.session.query(func.abs(func.sum(CoinsLedgerModel.coins))).filter(
            CoinsLedgerModel.user_id == current_user.id,
            CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.debit_usage,
            CoinsLedgerModel.created_at >= first_day_of_month
        ).scalar() or 0
        
        return UserCoinUsageResponse(
            available_coins=int(balance),
            this_month_usage=int(usage)
        )
    except Exception as e:
        logger.error(f"Error in get_user_coin_usage: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch coin usage data: {str(e)}"
        )

@router.get("/coins/buckets", response_model=CoinBucketsResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_coin_buckets(
    page: int = 1,
    size: int = 10,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    try:
        skip = (page - 1) * size

        base_query = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == current_user.id,
            CoinsLedgerModel.remaining_coins > 0,
        )

        total_available = (
            db.session.query(func.sum(CoinsLedgerModel.remaining_coins))
            .filter(
                CoinsLedgerModel.user_id == current_user.id,
                CoinsLedgerModel.remaining_coins > 0,
            )
            .scalar() or 0
        )

        total_count = base_query.count()

        buckets_query = (
            base_query
            .order_by(CoinsLedgerModel.created_at.asc())
            .offset(skip)
            .limit(size)
            .all()
        )

        reference_ids = [item.reference_id for item in buckets_query if item.reference_id]

        payments = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.id.in_(reference_ids))
            .all()
        )
        payment_map = {p.id: p for p in payments}

        buckets = []

        for item in buckets_query:
            source_name = "Coins"

            payment = payment_map.get(item.reference_id)
            if payment and payment.payment_type in (PaymentTypeEnum.coin_purchase, PaymentTypeEnum.addon):
                source_name = "Credit Purchase"

            buckets.append(
                CoinBucketItem(
                    source=source_name,
                    amount=item.remaining_coins,
                )
            )

        total_pages = ceil(total_count / size) if size > 0 else 1

        return CoinBucketsResponse(
            total=total_count,
            page=page,
            size=size,
            pages=total_pages,
            buckets=buckets,
            total_available=total_available
        )

    except Exception as e:
        logger.exception("Error fetching coin buckets")
        raise HTTPException(status_code=500, detail="Failed to fetch coin buckets")

@router.get("/agents-summary", response_model=List[AgentSummaryItem], openapi_extra={"security":[{"BearerAuth":[]}]})
def get_agents_summary(current_user: UnifiedAuthModel = Depends(require_active_user())):
    """Per-agent summary for the logged-in user: web-agent / widget counts and
    conversation success / failed counts."""
    try:
        return build_agent_summaries(current_user.id)
    except Exception as e:
        logger.error(f"Error in get_agents_summary: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage-history", response_model=UsageHistoryResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_usage_history(
    page: int = 1,
    size: int = 10,
    current_user: UnifiedAuthModel = Depends(require_active_user())
):
    """Details coin usage transactions."""
    try:
        skip = (page - 1) * size

        # All coin transactions (credits added, deductions, expiries, refunds,
        # admin adjustments, resets) — not just usage — so the balance changes
        # in the running history are fully accounted for.
        base_query = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == current_user.id,
        )

        total_count = base_query.count()

        history_query = base_query.order_by(CoinsLedgerModel.created_at.desc()).offset(skip).limit(size).all()

        action_map = {
            "debit_usage": "AI Interaction",
            "credit_subscription": "Subscription Credits",
            "credit_purchase": "Credits Purchased",
            "refund": "Refund",
            "expired": "Coins Expired",
            "carry_forward_reset": "Unused Coins Reset",
            "admin_adjustment": "Admin Adjustment",
        }

        history = []
        for item in history_query:
            agent_name = "System"
            if item.reference_type == "conversation" and item.reference_id:
                conv = db.session.query(ConversationsModel).filter(ConversationsModel.id == item.reference_id).first()
                if conv and conv.agent:
                    agent_name = conv.agent.agent_name

            source_name = str(item.transaction_type.value if hasattr(item.transaction_type, 'value') else item.transaction_type)
            friendly_action = action_map.get(source_name, source_name.replace("_", " ").title())

            history.append(UsageHistoryItem(
                date_time=item.created_at,
                action=friendly_action,
                transaction_type=source_name,
                agent_name=agent_name,
                coins=item.coins,                              # signed
                balance_before=item.balance_after - item.coins,
                balance_after=item.balance_after,
                reason=item.notes,
            ))
            
        total_pages = ceil(total_count / size) if size > 0 else 1

        return UsageHistoryResponse(
            total=total_count,
            page=page,
            size=size,
            pages=total_pages,
            history=history
        )
    except Exception as e:
        logger.error(f"Error in get_usage_history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/billing-history", response_model=BillingHistoryResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_billing_history(
    page: int = 1,
    size: int = 10,
    current_user: UnifiedAuthModel = Depends(require_active_user())
):
    """
    Lists past payments plus non-payment subscription lifecycle events
    (paused, cancelled by the user, or cancelled by admin because the plan
    was deactivated/deleted) — merged and sorted by date since they come
    from two different tables (PaymentModel and ActivityLogModel).
    """
    try:
        skip = (page - 1) * size

        payments = db.session.query(PaymentModel).filter(
            PaymentModel.user_id == current_user.id
        ).all()

        billing_events = db.session.query(ActivityLogModel).filter(
            ActivityLogModel.user_id == current_user.id,
            ActivityLogModel.event_type.in_(SUBSCRIPTION_BILLING_EVENT_TYPES),
        ).all()

        dated_items = []
        for p in payments:
            if p.payment_type in (PaymentTypeEnum.coin_purchase, PaymentTypeEnum.addon):
                coins = p.metadata_json.get("coins") if p.metadata_json else None
                description = f"Credit Purchase ({coins} credits)" if coins else "Credit Purchase"
            elif p.payment_type == PaymentTypeEnum.subscription:
                description = "Subscription Payment"
            else:
                description = "Miscellaneous Payment"

            dated_items.append((p.created_at, BillingHistoryItem(
                date=p.created_at,
                description=description,
                amount=p.amount,
                currency=p.currency,
                status=p.status,
                invoice_url=p.invoice_url
            )))

        for log in billing_events:
            dated_items.append((log.created_at, BillingHistoryItem(
                date=log.created_at,
                description=log.description,
                amount=0.0,
                currency="",
                status=SUBSCRIPTION_BILLING_EVENT_STATUS_LABELS.get(log.event_type, log.event_type),
                invoice_url=None
            )))

        dated_items.sort(key=lambda item: item[0], reverse=True)

        total_count = len(dated_items)
        total_pages = ceil(total_count / size) if size > 0 else 1
        history = [item for _, item in dated_items[skip: skip + size]]

        return BillingHistoryResponse(
            total=total_count,
            page=page,
            size=size,
            pages=total_pages,
            history=history
        )
    except Exception as e:
        logger.error(f"Error in get_billing_history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/public-api/usage", response_model=PublicAPIUsageResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_public_api_usage(request:Request,current_user: UnifiedAuthModel = Depends(require_active_user())):
    """Returns public API usage metrics and last 7 days for bar graph."""
    try:
        first_day_of_month, first_day_prev_month = get_current_and_previous_month_start()

        now = datetime.now(timezone.utc)
        last_24h = now - timedelta(hours=24)
        
        # Scoped to channel=public_api so websocket call rows (added for the
        # Logs page) don't inflate these "Developer API" HTTP-only metrics.
        total_api_calls_this_month = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel == PublicLogChannelEnum.public_api,
            APICallLogModel.created_at >= first_day_of_month
        ).scalar() or 0

        total_api_calls_prev_month = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel == PublicLogChannelEnum.public_api,
            APICallLogModel.created_at >= first_day_prev_month,
            APICallLogModel.created_at < first_day_of_month
        ).scalar() or 0
        total_api_calls_this_month_change = calculate_percentage_change(total_api_calls_this_month, total_api_calls_prev_month)

        api_coins_used_this_month = db.session.query(func.sum(APICallLogModel.coins_used)).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel == PublicLogChannelEnum.public_api,
            APICallLogModel.created_at >= first_day_of_month
        ).scalar() or 0

        avg_api_response_time_24h = db.session.query(func.avg(APICallLogModel.response_time_ms)).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel == PublicLogChannelEnum.public_api,
            APICallLogModel.created_at >= last_24h
        ).scalar() or 0.0

        seven_days_ago = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        usage_records = db.session.query(APIDailyUsageModel).filter(
            APIDailyUsageModel.user_id == current_user.id,
            APIDailyUsageModel.usage_date >= seven_days_ago
        ).order_by(APIDailyUsageModel.usage_date.asc()).all()
        
        usage_map = {str(r.usage_date.date()): r.hit_count for r in usage_records}
        
        daily_usage = []
        for i in range(7):
            date = (seven_days_ago + timedelta(days=i)).date()
            date_str = str(date)
            daily_usage.append(APIUsageDailyItem(
                date=date_str,
                count=usage_map.get(date_str, 0)
            ))
        apis = [
            APIListItem(
                path= api["path"],
                method = api["method"],
                description = api["description"],
                swagger_link = str(request.base_url)+api["swagger_link"]
            ) for api in api_list
        ]
            
        return PublicAPIUsageResponse(
            total_api_calls_this_month=total_api_calls_this_month,
            total_api_calls_this_month_change=float(total_api_calls_this_month_change),
            api_coins_used_this_month=int(api_coins_used_this_month),
            avg_api_response_time_24h=round(float(avg_api_response_time_24h), 2),
            daily_usage=daily_usage,
            api_list=apis
        )
    except Exception as e:
        logger.error(f"Error in get_public_api_usage: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api-logs", response_model=UserAPICallLogResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_api_logs(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(require_active_user())
):
    """Returns detailed public API call logs for the user."""
    try:
        skip = (page - 1) * size
        
        base_query = db.session.query(APICallLogModel).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel == PublicLogChannelEnum.public_api,
        )
        
        total_count = base_query.count()
        logs = base_query.order_by(APICallLogModel.created_at.desc()).offset(skip).limit(size).all()
        
        total_pages = ceil(total_count / size) if size > 0 else 1
        
        return UserAPICallLogResponse(
            total=total_count,
            page=page,
            size=size,
            pages=total_pages,
            logs=[UserAPICallLogItem.model_validate(log) for log in logs]
        )
    except Exception as e:
        logger.error(f"Error in get_user_api_logs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

PUBLIC_LOG_CHANNELS = [
    PublicLogChannelEnum.public_api,
    PublicLogChannelEnum.public_websocket,
    PublicLogChannelEnum.widget_websocket,
]


def _parse_month_param(month: Optional[str]) -> tuple:
    """Returns (year, month) for a "YYYY-MM" string, defaulting to the current UTC month."""
    if month:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
            return parsed.year, parsed.month
        except ValueError:
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
    now = datetime.now(timezone.utc)
    return now.year, now.month


def _day_of_month_graph(base_filters: list, year: int, month: int) -> PublicLogGraphResponse:
    """Builds the full day-of-month range for `month`, filling gaps with 0 —
    mirrors the fill-the-full-range pattern used by /analytics's daily trends."""
    days_in_month = calendar.monthrange(year, month)[1]
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    month_end = (
        datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
        else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    )

    rows = (
        db.session.query(
            func.extract("day", APICallLogModel.created_at).label("day"),
            func.sum(case((APICallLogModel.is_success == True, 1), else_=0)).label("success_count"),
            func.sum(case((APICallLogModel.is_success == False, 1), else_=0)).label("failure_count"),
        )
        .filter(*base_filters, APICallLogModel.created_at >= month_start, APICallLogModel.created_at < month_end)
        .group_by("day")
        .all()
    )
    by_day = {int(r.day): r for r in rows}
    buckets = [
        DayOfMonthBucket(
            day=d,
            success_count=int(by_day[d].success_count or 0) if d in by_day else 0,
            failure_count=int(by_day[d].failure_count or 0) if d in by_day else 0,
        )
        for d in range(1, days_in_month + 1)
    ]
    return PublicLogGraphResponse(month=f"{year:04d}-{month:02d}", buckets=buckets)


@router.get("/public-logs/endpoints", response_model=PublicLogEndpointListResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_public_log_endpoints(current_user: UnifiedAuthModel = Depends(require_active_user())):
    """
    All-time success/failure/total counts per (channel, route, method) across
    this user's public API + public websocket surfaces — backs the Logs
    page's endpoint table. All-time (not month-scoped) so a chronically
    broken endpoint set up months ago still surfaces here; the month view
    lives in the graph endpoints below.
    """
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
            .filter(
                APICallLogModel.user_id == current_user.id,
                APICallLogModel.channel.in_(PUBLIC_LOG_CHANNELS),
            )
            .group_by(APICallLogModel.channel, APICallLogModel.api_route, APICallLogModel.method)
            .all()
        )
        endpoints = [
            PublicLogEndpointItem(
                channel=row.channel.value if row.channel else PublicLogChannelEnum.public_api.value,
                route=row.api_route,
                method=row.method,
                success_count=int(row.success_count or 0),
                failure_count=int(row.failure_count or 0),
                total_count=int(row.total_count or 0),
            )
            for row in rows
        ]
        return PublicLogEndpointListResponse(endpoints=endpoints)
    except Exception as e:
        logger.error(f"Error in get_public_log_endpoints: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/public-logs/summary-graph", response_model=PublicLogGraphResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_public_log_summary_graph(month: Optional[str] = None, current_user: UnifiedAuthModel = Depends(require_active_user())):
    """Day-of-month success/failure counts across all this user's public endpoints, for `month` (YYYY-MM, default current month)."""
    try:
        year, mon = _parse_month_param(month)
        base_filters = [
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel.in_(PUBLIC_LOG_CHANNELS),
        ]
        return _day_of_month_graph(base_filters, year, mon)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_public_log_summary_graph: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/public-logs/overview", response_model=PublicLogOverviewResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_public_log_overview(current_user: UnifiedAuthModel = Depends(require_active_user())):
    """All-time total/success/failure call counts across this user's public API + public websocket surfaces."""
    try:
        row = (
            db.session.query(
                func.count(APICallLogModel.id).label("total_calls"),
                func.sum(case((APICallLogModel.is_success == True, 1), else_=0)).label("success_count"),
                func.sum(case((APICallLogModel.is_success == False, 1), else_=0)).label("failure_count"),
            )
            .filter(
                APICallLogModel.user_id == current_user.id,
                APICallLogModel.channel.in_(PUBLIC_LOG_CHANNELS),
            )
            .first()
        )
        return PublicLogOverviewResponse(
            total_calls=int(row.total_calls or 0),
            success_count=int(row.success_count or 0),
            failure_count=int(row.failure_count or 0),
        )
    except Exception as e:
        logger.error(f"Error in get_public_log_overview: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/public-logs/hourly-distribution", response_model=List[HourlyDistribution], openapi_extra={"security":[{"BearerAuth":[]}]})
def get_public_log_hourly_distribution(current_user: UnifiedAuthModel = Depends(require_active_user())):
    """All-time hour-of-day distribution of this user's public API + public websocket calls, for spotting peak usage times."""
    try:
        hourly_data = (
            db.session.query(
                func.extract("hour", APICallLogModel.created_at).label("hour"),
                func.count(APICallLogModel.id).label("count"),
            )
            .filter(
                APICallLogModel.user_id == current_user.id,
                APICallLogModel.channel.in_(PUBLIC_LOG_CHANNELS),
            )
            .group_by("hour")
            .all()
        )

        def format_hour(h):
            h = int(h)
            if h == 0: return "12 AM"
            if h == 12: return "12 PM"
            if h < 12: return f"{h} AM"
            return f"{h-12} PM"

        by_hour = {int(r.hour): int(r.count) for r in hourly_data}
        return [
            HourlyDistribution(hour=h, time_label=format_hour(h), count=by_hour.get(h, 0))
            for h in range(24)
        ]
    except Exception as e:
        logger.error(f"Error in get_public_log_hourly_distribution: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/public-logs/logs", response_model=PublicLogListResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_public_logs(
    channel: PublicLogChannelEnum,
    route: str,
    page: int = 1,
    size: int = 20,
    only_failures: bool = True,
    api_key_id: Optional[int] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Paginated call log rows (full request/response detail) for one endpoint; defaults to failures only."""
    try:
        skip = (page - 1) * size
        base_query = db.session.query(APICallLogModel).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel == channel,
            APICallLogModel.api_route == route,
        )
        if only_failures:
            base_query = base_query.filter(APICallLogModel.is_success == False)
        if api_key_id is not None:
            base_query = base_query.filter(APICallLogModel.api_key_id == api_key_id)

        total_count = base_query.count()
        logs = base_query.order_by(APICallLogModel.created_at.desc()).offset(skip).limit(size).all()
        total_pages = ceil(total_count / size) if size > 0 else 1

        key_ids = {log.api_key_id for log in logs if log.api_key_id}
        key_names = {}
        if key_ids:
            key_names = {
                k.id: k.name or k.client_id
                for k in db.session.query(APIKeyModel).filter(APIKeyModel.id.in_(key_ids)).all()
            }

        return PublicLogListResponse(
            total=total_count,
            page=page,
            size=size,
            pages=total_pages,
            items=[
                PublicLogItem(
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
                )
                for log in logs
            ],
        )
    except Exception as e:
        logger.error(f"Error in get_public_logs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/public-logs/graph", response_model=PublicLogGraphResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_public_log_graph(
    channel: PublicLogChannelEnum,
    route: str,
    month: Optional[str] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Day-of-month success/failure counts scoped to one (channel, route), for `month` (YYYY-MM, default current month)."""
    try:
        year, mon = _parse_month_param(month)
        base_filters = [
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.channel == channel,
            APICallLogModel.api_route == route,
        ]
        return _day_of_month_graph(base_filters, year, mon)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_public_log_graph: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/leads", response_model=DashboardLeadListResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_leads(
    page: int = 1,
    size: int = 20,
    search: Optional[str] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user())
):
    try:
        skip = (page - 1) * size
        
        query = db.session.query(WidgetLeadModel).join(
            WidgetModel, WidgetLeadModel.widget_id == WidgetModel.id
        ).filter(
            WidgetModel.user_id == current_user.id
        )
        
        if search:
            term = f"%{search}%"
            query = query.filter(
                (WidgetLeadModel.name.ilike(term)) |
                (WidgetLeadModel.email.ilike(term)) |
                (WidgetLeadModel.phone.ilike(term))
            )
            
        total = query.count()
        leads = query.order_by(WidgetLeadModel.created_at.desc()).offset(skip).limit(size).all()
        
        total_pages = ceil(total / size) if size > 0 else 1
        
        return DashboardLeadListResponse(
            total=total,
            page=page,
            size=size,
            pages=total_pages,
            leads=[
                DashboardLeadItem(
                    id=lead.id,
                    widget_id=lead.widget_id,
                    widget_name=lead.widget.widget_name,
                    widget_public_id=lead.widget.public_id if lead.widget else None,
                    name=lead.name,
                    email=lead.email,
                    phone=lead.phone,
                    custom_data=lead.custom_data,
                    created_at=lead.created_at,
                    duration=lead.conversation.duration if lead.conversation and lead.conversation.duration else 0
                ) for lead in leads
            ]
        )
    except Exception as e:
        logger.error(f"Error in get_user_leads: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user leads: {str(e)}"
        )