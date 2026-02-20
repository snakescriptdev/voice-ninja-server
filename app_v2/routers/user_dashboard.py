from fastapi import APIRouter, status, Depends,HTTPException
from fastapi_sqlalchemy import db
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, ActivityLogModel
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.user_dashboard import UserDashboardAgentResponse,UserDashboardPhoneNumberResponse
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