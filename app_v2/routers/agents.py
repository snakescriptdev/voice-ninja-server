from fastapi import APIRouter, HTTPException, status
from databases.models import AgentModel
from app_v2.core.logger import setup_logger
from schemas.agent_schema import AgentRequestSchema,AgentResponseModel
from app_v2.constants import STATUS_SUCCESS

logger = setup_logger(__name__)


router = APIRouter(prefix="api/v2/agent",tags=["Agent API's"])


#apis for router



#create a agent api

@router.put("/create",response_model=AgentResponseModel)
async def create_agent(agent:AgentRequestSchema):
    try:
        agent =AgentModel.create(**agent.model_dump())
        return {
            "status":STATUS_SUCCESS,
            "status_code":status.HTTP_201_CREATED,
            "agent": agent
        }
    except Exception:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,"agent creation failed at the moment")