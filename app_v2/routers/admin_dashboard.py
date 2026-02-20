from fastapi import APIRouter, HTTPException, status, Depends
from app_v2.utils.jwt_utils import is_admin
from datetime import datetime
from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel
from app_v2.schemas.activity_schema import ActivityLogResponse
from app_v2.core.logger import setup_logger
from fastapi_sqlalchemy import db
from sqlalchemy import func
from app_v2.utils.time_utils import format_time_ago
# from elevenlabs


logger = setup_logger(__name__)


router = APIRouter(prefix="/admin/dashboard",tags=["Admin"])

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








#eleven labs useage tracking
