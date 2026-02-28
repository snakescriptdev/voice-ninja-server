from fastapi import APIRouter, status, Depends,HTTPException
from fastapi_sqlalchemy import db
from datetime import datetime
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel, ConversationsModel,PlanModel,UserSubscriptionModel, CoinsLedgerModel
from app_v2.schemas.enum_types import CoinTransactionTypeEnum
from app_v2.utils.coin_utils import get_user_coin_balance

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
    UserCoinUsageResponse
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

@router.get("/analytics", response_model=UserAnalyticsResponse, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_analytics(current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        # 1. Overall stats
        total_calls = db.session.query(func.count(ConversationsModel.id)).filter(
            ConversationsModel.user_id == current_user.id
        ).scalar() or 0
        
        avg_duration = db.session.query(func.avg(ConversationsModel.duration)).filter(
            ConversationsModel.user_id == current_user.id
        ).scalar() or 0.0
        
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
            func.avg(ConversationsModel.duration).label('avg_duration')
        ).join(ConversationsModel, AgentModel.id == ConversationsModel.agent_id)\
         .filter(ConversationsModel.user_id == current_user.id)\
         .group_by(AgentModel.id, AgentModel.agent_name).all()
        
        agent_list = [
            AgentAnalytics(
                agent_id=a.agent_id,
                agent_name=a.agent_name,
                call_count=a.call_count,
                avg_duration=round(float(a.avg_duration or 0), 2)
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
            avg_call_duration=round(float(avg_duration), 2),
            hourly_distribution=hourly_list,
            agent_analytics=agent_list,
            channel_distribution=channel_list
        )
        
    except Exception as e:
        logger.error(f"Error in get_user_analytics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch analytics data: {str(e)}"
        )


def map_subcription_response_db(plan:PlanModel,user_subscription:UserSubscriptionModel):
    return UserSubscriptionResponse(
        plan_id=plan.id,
        plan_name=plan.display_name,
        coins_included=plan.coins_included,
        price=plan.price,
        billing_period=plan.billing_period,
        current_period_end=user_subscription.current_period_end
    )
    
@router.get("/get-user-subscription",openapi_extra={"security":[{"BearerAuth":[]}]},response_model=UserSubscriptionResponse )
def user_subscription(current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        logger.info(f"Fetching user subscription for user: {current_user.id}")
        user_subscription = db.session.query(UserSubscriptionModel).filter(UserSubscriptionModel.user_id == current_user.id).first()
        if not user_subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User subscription not found"
            )
        logger.info(f"User subscription found: {user_subscription}")
        return map_subcription_response_db(user_subscription.plan,user_subscription)
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