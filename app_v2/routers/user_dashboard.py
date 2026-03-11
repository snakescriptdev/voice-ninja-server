from fastapi import APIRouter, status, Depends,HTTPException
from fastapi.responses import HTMLResponse
import os
from fastapi.requests import Request
from fastapi_sqlalchemy import db
from datetime import datetime, timedelta
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.utils.feature_access import RequireFeature
from app_v2.databases.models import (
    UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel, 
    ConversationsModel, PlanModel, UserSubscriptionModel, CoinsLedgerModel, 
    PaymentModel, WebAgentModel, WebAgentLeadModel,APIDailyUsageModel,CoinPackageModel,
    APICallLogModel
)
from app_v2.utils.analytics_utils import calculate_percentage_change, get_current_and_previous_month_start
from sqlalchemy import or_
from app_v2.schemas.enum_types import CoinTransactionTypeEnum, PaymentStatusEnum,SubscriptionStatusEnum,PaymentTypeEnum
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
    UserSubscriptionResponse,
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
    APIListItem
)
from app_v2.core.logger import setup_logger
from app_v2.utils.time_utils import format_time_ago
from math import ceil

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(prefix="/api/v2/user-dashboard", tags=["User Dashboard"], dependencies=[Depends(security)])





@router.get("/agents-data", status_code=status.HTTP_200_OK,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_agents_data(skip: int = 0, limit: int = 3, current_user: str = Depends(get_current_user)):
    # try fetching the no of agents user has created
    try:
        count = db.session.query(AgentModel).filter(
            AgentModel.user_id == current_user.id,
            AgentModel.is_enabled.is_(True)
            ).count()
        # now we need to fetch agents data
        agents = db.session.query(AgentModel).filter(
            AgentModel.user_id == current_user.id,
            AgentModel.is_enabled.is_(True)
            ).order_by(AgentModel.created_at.desc()).offset(skip).limit(limit).all()
        
        #prepare page metadata
        total_pages = ceil(count / limit)
        current_page = skip // limit + 1
        return PaginatedResponse(
            total=count,
            page=current_page,
            size=limit,
            pages=total_pages,
            items=[UserDashboardAgentResponse(id=agent.id, agent_name=agent.agent_name, is_enabled=agent.is_enabled) for agent in agents]
        )
    except Exception as e:
        logger.error(f"error while fetching the agents data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to fetch the agents data at the moment:{str(e)}"
        )
        

@router.get("/phone-numbers", status_code=status.HTTP_200_OK,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_phone_numbers(skip: int = 0, limit: int = 3, current_user: str = Depends(get_current_user)):
    try:
        count = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.user_id == current_user.id
            ).count()
        # now we need to fetch phone numbers data
        phone_numbers = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.user_id == current_user.id
            ).order_by(PhoneNumberService.created_at.desc()).offset(skip).limit(limit).all()
        
        #prepare page metadata
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
    current_user: UnifiedAuthModel = Depends(get_current_user)
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

        # 1. Overall stats (All-time)
        total_calls = db.session.query(func.count(ConversationsModel.id)).filter(
            ConversationsModel.user_id == current_user.id
        ).scalar() or 0
        
        avg_duration = db.session.query(func.avg(ConversationsModel.duration)).filter(
            ConversationsModel.user_id == current_user.id
        ).scalar() or 0.0

        # Change for calls (this month vs last month)
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

        # Change for avg duration (this month vs last month)
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

        # 1.1 New Metrics
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

        active_leads_count = db.session.query(func.count(WebAgentLeadModel.id)).join(
            WebAgentModel, WebAgentLeadModel.web_agent_id == WebAgentModel.id
        ).filter(
            WebAgentModel.user_id == current_user.id
        ).scalar() or 0
        
        curr_leads = db.session.query(func.count(WebAgentLeadModel.id)).join(
            WebAgentModel, WebAgentLeadModel.web_agent_id == WebAgentModel.id
        ).filter(
            WebAgentModel.user_id == current_user.id,
            WebAgentLeadModel.created_at >= first_day_of_month
        ).scalar() or 0
        
        prev_leads = db.session.query(func.count(WebAgentLeadModel.id)).join(
            WebAgentModel, WebAgentLeadModel.web_agent_id == WebAgentModel.id
        ).filter(
            WebAgentModel.user_id == current_user.id,
            WebAgentLeadModel.created_at >= first_day_prev_month,
            WebAgentLeadModel.created_at < first_day_of_month
        ).scalar() or 0
        active_leads_count_change = calculate_percentage_change(curr_leads, prev_leads)
        
        # 1.2 Trend Data (Last 7 Days)
        now = datetime.utcnow()
        seven_days_ago = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)

        # Helper to get daily counts
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

        # Call trends
        call_daily = get_daily_counts(ConversationsModel, ConversationsModel.user_id, ConversationsModel.created_at)
        # Coin trends
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

        # 2. Hourly distribution
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
        
        # 3. Agent analytics
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
        
        # 4. Channel distribution
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



    
@router.get("/get-user-subscription", response_model=UserSubscriptionResponse, openapi_extra={"security": [{"BearerAuth": []}]})
def user_subscription(current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        logger.info(f"Fetching user subscription for user: {current_user.id}")

        # Priority 1: clean active/paused subscription (not pending cancel or update)
        user_subscription = (
            db.session.query(UserSubscriptionModel)
            .filter(
                UserSubscriptionModel.user_id == current_user.id,
                or_(
                    UserSubscriptionModel.status == SubscriptionStatusEnum.active,
                    UserSubscriptionModel.status == SubscriptionStatusEnum.paused,
                ),
                UserSubscriptionModel.cancel_at_period_end == False,
            )
            .order_by(UserSubscriptionModel.created_at.desc())
            .first()
        )

        # Priority 2: active/paused but cancel or update is in-flight
        if not user_subscription:
            user_subscription = (
                db.session.query(UserSubscriptionModel)
                .filter(
                    UserSubscriptionModel.user_id == current_user.id,
                    or_(
                        UserSubscriptionModel.status == SubscriptionStatusEnum.active,
                        UserSubscriptionModel.status == SubscriptionStatusEnum.paused,
                    ),
                )
                .order_by(UserSubscriptionModel.created_at.desc())
                .first()
            )

        # Priority 3: last resort — show most recent regardless of status (expired/cancelled)
        if not user_subscription:
            user_subscription = (
                db.session.query(UserSubscriptionModel)
                .filter(UserSubscriptionModel.user_id == current_user.id)
                .order_by(UserSubscriptionModel.created_at.desc())
                .first()
            )

        if not user_subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User subscription not found"
            )

        plan = user_subscription.plan
        logger.info(f"User subscription found: {user_subscription}")
        return UserSubscriptionResponse(
            # ---- subscription ----
            subscription_id=user_subscription.id,
            status=user_subscription.status,
            current_period_start=user_subscription.current_period_start,
            current_period_end=user_subscription.current_period_end,
            cancel_at_period_end=user_subscription.cancel_at_period_end,
            provider=user_subscription.provider,
            provider_subscription_id=user_subscription.provider_subscription_id,
            marked_for_update=True if user_subscription.next_plan_id else False,
            next_plan_id=user_subscription.next_plan_id or None,

            # ---- plan ----
            plan_id=plan.id,
            plan_name=plan.display_name,
            description=plan.description,
            price=plan.price,
            currency=plan.currency,
            coins_included=plan.coins_included,
            carry_forward_coins=plan.carry_forward_coins,
            billing_period=plan.billing_period,
            icon=plan.icon,
            gradient_color=plan.gradient_color,
            mark_as_popular=plan.mark_as_popular,
            is_active=plan.is_active,

            # ---- features ----
            features=plan.features
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in user_subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user subscription data: {str(e)}"
        )

@router.get("/coin-usage", response_model=UserCoinUsageResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_coin_usage(current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        # 1. Get current balance
        balance = get_user_coin_balance(current_user.id)
        
        # 2. Get this month's usage
        now = datetime.utcnow()
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
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        skip = (page - 1) * size
        now = datetime.utcnow()

        base_query = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == current_user.id,
            CoinsLedgerModel.remaining_coins > 0,
            or_(
                CoinsLedgerModel.expiry_at.is_(None),
                CoinsLedgerModel.expiry_at > now
            )
        )

        total_available = (
            db.session.query(func.sum(CoinsLedgerModel.remaining_coins))
            .filter(
                CoinsLedgerModel.user_id == current_user.id,
                CoinsLedgerModel.remaining_coins > 0,
                or_(
                    CoinsLedgerModel.expiry_at.is_(None),
                    CoinsLedgerModel.expiry_at > now
                )
            )
            .scalar() or 0
        )

        total_count = base_query.count()

        buckets_query = (
            base_query
            .order_by(CoinsLedgerModel.expiry_at.asc().nulls_last())
            .offset(skip)
            .limit(size)
            .all()
        )

        reference_ids = [item.reference_id for item in buckets_query if item.reference_id]

        # Fetch subscriptions FIRST
        subscriptions = (
            db.session.query(UserSubscriptionModel)
            .filter(UserSubscriptionModel.id.in_(reference_ids))
            .all()
        )
        subscription_map = {s.id: s for s in subscriptions}

        # Fetch payments
        payments = (
            db.session.query(PaymentModel)
            .filter(PaymentModel.id.in_(reference_ids))
            .all()
        )
        payment_map = {p.id: p for p in payments}

        # Extract bundle ids
        bundle_ids = []
        for p in payments:
            metadata = p.metadata_json or {}
            if p.payment_type == PaymentTypeEnum.coin_purchase:
                bundle_id = metadata.get("bundle_id")
                if bundle_id:
                    bundle_ids.append(bundle_id)

        bundles = (
            db.session.query(CoinPackageModel)
            .filter(CoinPackageModel.id.in_(bundle_ids))
            .all()
        )
        bundle_map = {b.id: b for b in bundles}

        buckets = []
        now = datetime.utcnow()

        for item in buckets_query:

            source_name = "Coins"

            # ✅ PRIORITY 1 — Subscription
            sub = subscription_map.get(item.reference_id)
            if sub and sub.plan:
                source_name = f"{sub.plan.display_name} Subscription"

            # ✅ PRIORITY 2 — Bundle purchase
            else:
                payment = payment_map.get(item.reference_id)

                if payment and payment.payment_type == PaymentTypeEnum.coin_purchase:
                    metadata = payment.metadata_json or {}
                    bundle_id = metadata.get("bundle_id")
                    bundle = bundle_map.get(bundle_id)

                    if bundle:
                        source_name = bundle.name

            status = None
            if item.expiry_at and now <= item.expiry_at <= now + timedelta(days=7):
                status = "expiring soon"

            buckets.append(
                CoinBucketItem(
                    source=source_name,
                    amount=item.remaining_coins,
                    expiry_date=item.expiry_at,
                    status=status
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

@router.get("/usage-history", response_model=UsageHistoryResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_usage_history(
    page: int = 1,
    size: int = 10,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Details coin usage transactions."""
    try:
        skip = (page - 1) * size

        # Fetch usage (debit) transactions
        base_query = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == current_user.id,
            CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.debit_usage
        )

        total_count = base_query.count()

        history_query = base_query.order_by(CoinsLedgerModel.created_at.desc()).offset(skip).limit(size).all()
        
        # We need agent names for the records. Reference ID in debit_usage is often the conversation ID.
        # However, the ledger doesn't always have direct agent link. 
        # But we can try to look it up if reference_type is available or just list the action.
        
        history = []
        for item in history_query:
            agent_name = "System"
            # Try to find agent if this was a conversation
            if item.reference_type == "conversation" and item.reference_id:
                conv = db.session.query(ConversationsModel).filter(ConversationsModel.id == item.reference_id).first()
                if conv and conv.agent:
                    agent_name = conv.agent.agent_name
            
            action_map = {
                "debit_usage": "AI Interaction",
                "expired": "Coins Expired",
                "carry_forward_reset": "Unused Coins Reset"
            }
            source_name = str(item.transaction_type.value if hasattr(item.transaction_type, 'value') else item.transaction_type)
            friendly_action = action_map.get(source_name, source_name.replace("_", " ").title())

            history.append(UsageHistoryItem(
                date_time=item.created_at,
                action=friendly_action,
                agent_name=agent_name,
                coins_used=abs(item.coins),
                balance=item.balance_after
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
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Lists past payments and billing events."""
    try:
        skip = (page - 1) * size

        base_query = db.session.query(PaymentModel).filter(
            PaymentModel.user_id == current_user.id
        )

        total_count = base_query.count()

        payments = base_query.order_by(PaymentModel.created_at.desc()).offset(skip).limit(size).all()
        
        # Pre-fetch plans and bundles for descriptions
        plans = {p.id: p.display_name for p in db.session.query(PlanModel).all()}
        from app_v2.databases.models import CoinPackageModel
        bundles = {b.id: b.name for b in db.session.query(CoinPackageModel).all()}
        
        history = []
        for p in payments:
            description = "Miscellaneous Payment"
            from app_v2.schemas.enum_types import PaymentTypeEnum
            if p.payment_type == PaymentTypeEnum.subscription:
                p_id = p.metadata_json.get("plan_id") if p.metadata_json else None
                plan_name = plans.get(p_id, "Monthly Subscription")
                description = f"Subscription: {plan_name}"
            elif p.payment_type in [PaymentTypeEnum.coin_purchase, PaymentTypeEnum.addon]:
                b_id = p.metadata_json.get("bundle_id") if p.metadata_json else None
                bundle_name = bundles.get(b_id, "Coin Bundle")
                description = f"Purchase: {bundle_name}"
            
            history.append(BillingHistoryItem(
                date=p.created_at,
                description=description,
                amount=p.amount,
                currency=p.currency,
                status=p.status,
                invoice_url=p.invoice_url
            ))
            
        total_pages = ceil(total_count / size) if size > 0 else 1

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
def get_public_api_usage(request:Request,current_user: UnifiedAuthModel = Depends(get_current_user)):
    """Returns public API usage metrics and last 7 days for bar graph."""
    try:
        first_day_of_month, first_day_prev_month = get_current_and_previous_month_start()

        now = datetime.utcnow()
        last_24h = now - timedelta(hours=24)
        
        # API Metrics
        total_api_calls_this_month = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.created_at >= first_day_of_month
        ).scalar() or 0
        
        total_api_calls_prev_month = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.created_at >= first_day_prev_month,
            APICallLogModel.created_at < first_day_of_month
        ).scalar() or 0
        total_api_calls_this_month_change = calculate_percentage_change(total_api_calls_this_month, total_api_calls_prev_month)

        api_coins_used_this_month = db.session.query(func.sum(APICallLogModel.coins_used)).filter(
            APICallLogModel.user_id == current_user.id,
            APICallLogModel.created_at >= first_day_of_month
        ).scalar() or 0

        avg_api_response_time_24h = db.session.query(func.avg(APICallLogModel.response_time_ms)).filter(
            APICallLogModel.user_id == current_user.id,
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
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Returns detailed public API call logs for the user."""
    try:
        skip = (page - 1) * size
        
        base_query = db.session.query(APICallLogModel).filter(
            APICallLogModel.user_id == current_user.id
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
