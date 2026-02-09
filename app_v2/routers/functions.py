from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from app_v2.schemas.function_schema import (
    FunctionCreateSchema,
    FunctionRead,
    FunctionUpdateSchema
)
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import (
    FunctionModel,
    FunctionApiConfig,
    UnifiedAuthModel,
    AgentModel,
    AgentFunctionBridgeModel,
)
from app_v2.core.logger import setup_logger


logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(
    prefix="/api/v2/functions",
    tags=["functions"],dependencies=[
        Depends(security)
    ]
)


def function_to_read(function: FunctionModel) -> FunctionRead:
    return FunctionRead(
        id=function.id,
        name=function.name,
        description=function.description,
        api_config=function.api_endpoint_url,
        created_at=function.created_at,
        modified_at=function.modified_at,
        elevenlabs_tool_id=function.elevenlabs_tool_id,
       
    )

from app_v2.utils.elevenlabs.agent_utils import ElevenLabsAgent

@router.post(
    "/",
    response_model=FunctionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create function",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def create_function(
    fn_in: FunctionCreateSchema,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        # 1. Enforce Agent Dependency
        if not fn_in.agent_id:
            raise HTTPException(status_code=400, detail="Function creation requires an agent_id (Function Binding)")

        agent = (
            db.session.query(AgentModel)
            .filter(
                AgentModel.id == fn_in.agent_id,
                AgentModel.user_id == current_user.id,
            )
            .first()
        )

        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        # 2. Local DB Creation (Early Stage)
        fn = FunctionModel(
            name=fn_in.name,
            description=fn_in.description,
        )
        db.session.add(fn)
        db.session.flush()

        # 3. Handle API Config & Prepare ElevenLabs Tool Payload
        cfg = fn_in.api_config
        
        # Save API config to DB
        db.session.add(
            FunctionApiConfig(
                function_id=fn.id,
                endpoint_url=str(cfg.endpoint_url),
                http_method=cfg.http_method,
                timeout_ms=cfg.timeout_ms,
                headers=cfg.headers,
                query_params=cfg.query_params,
                llm_response_schema=cfg.llm_response_schema,
                response_variables=cfg.response_variables,
            )
        )

        # Construct the API Schema for ElevenLabs
        api_schema = {
            "url": str(cfg.endpoint_url),
            "method": cfg.http_method.upper(),
        }
        
        if cfg.headers:
            api_schema["headers"] = cfg.headers
            
        webhook_config = {
            "tool_config": {
                "name": fn_in.name,
                "description": fn_in.description,
                "type": "webhook",
                "api_schema": api_schema
            }
        }

        # 4. Create Tool in ElevenLabs
        elevenlabs_tool_id = None
        if webhook_config and agent.elevenlabs_agent_id:
            try:
                client = ElevenLabsAgent()
                tool_resp = client.create_tool(webhook_config)
                
                if tool_resp.status:
                    elevenlabs_tool_id = tool_resp.data.get("id")
                    fn.elevenlabs_tool_id = elevenlabs_tool_id
                    
                    # 5. Bind Tool to Agent in ElevenLabs
                    # Fetch current tools first
                    tools_resp = client.get_agent_tools(agent.elevenlabs_agent_id)
                    all_tool_ids = []
                    if tools_resp.status:
                        all_tool_ids = tools_resp.data.get("tool_ids", [])
                    
                    if elevenlabs_tool_id not in all_tool_ids:
                        all_tool_ids.append(elevenlabs_tool_id)
                        
                        client.update_agent(
                            agent_id=agent.elevenlabs_agent_id,
                            tool_ids=all_tool_ids
                        )
                        logger.info(f"Bound tool {elevenlabs_tool_id} to agent {agent.elevenlabs_agent_id}")
                else:
                    logger.error(f"Failed to create ElevenLabs tool: {tool_resp.error_message}")
                    raise HTTPException(
                        status_code=status.HTTP_424_FAILED_DEPENDENCY,
                        detail=f"ElevenLabs Tool Creation Failed: {tool_resp.error_message}"
                    )

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error syncing tool with ElevenLabs: {e}")
                raise HTTPException(status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=f"Failed to create/bind tool in ElevenLabs: {str(e)}")

        # 6. Create Bridge in DB
        speak_while = False
        speak_after = True

        if fn_in.agent_config:
            speak_while = fn_in.agent_config.speak_while_execution
            speak_after = fn_in.agent_config.speak_after_execution

        db.session.add(
            AgentFunctionBridgeModel(
                agent_id=agent.id,
                function_id=fn.id,
                speak_while_execution=speak_while,
                speak_after_execution=speak_after,
            )
        )

        db.session.commit()
        db.session.refresh(fn)

        logger.info(f"Function created and bound successfully | function_id={fn.id}")
        fn_read = function_to_read(fn)
        fn_read.agent_config = fn_in.agent_config
        return fn_read

    # ✅ HANDLE UNIQUE CONSTRAINT
    except IntegrityError as e:
        db.session.rollback()
        logger.warning(
            f"Duplicate function name attempted | name={fn_in.name}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Function with this name already exists"
        )

    except HTTPException:
        db.session.rollback()
        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error while creating function: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create function: {str(e)}"
        )

@router.get(
    "/",
    response_model=PaginatedResponse[FunctionRead],
    summary="Get all functions",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_all_functions(
    agent_id: int | None = None,
    skip: int = 0,
    limit: int = 20,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        import math
        if agent_id:
            # Check if agent exists and belongs to user
            agent = (
                db.session.query(AgentModel)
                .filter(
                    AgentModel.id == agent_id,
                    AgentModel.user_id == current_user.id,
                )
                .first()
            )
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

            # Fetch bridged functions
            query = (
                db.session.query(AgentFunctionBridgeModel)
                .filter(AgentFunctionBridgeModel.agent_id == agent_id)
            )
            
            total = query.count()
            
            bridges = (
                query
                .options(
                    selectinload(AgentFunctionBridgeModel.function).selectinload(
                        FunctionModel.api_endpoint_url
                    )
                )
                .offset(skip)
                .limit(limit)
                .all()
            )

            result = []
            for bridge in bridges:
                fn_read = function_to_read(bridge.function)
                fn_read.agent_config = {
                    "speak_while_execution": bridge.speak_while_execution,
                    "speak_after_execution": bridge.speak_after_execution,
                }
                result.append(fn_read)
            
            pages = math.ceil(total / limit) if limit > 0 else 1
            current_page = (skip // limit) + 1 if limit > 0 else 1
            
            return PaginatedResponse(
                total=total,
                page=current_page,
                size=limit,
                pages=pages,
                items=result
            )

        else:
            query = db.session.query(FunctionModel)
            total = query.count()
            
            functions = (
                query
                .options(selectinload(FunctionModel.api_endpoint_url))
                .offset(skip)
                .limit(limit)
                .all()
            )
            
            pages = math.ceil(total / limit) if limit > 0 else 1
            current_page = (skip // limit) + 1 if limit > 0 else 1
            
            return PaginatedResponse(
                total=total,
                page=current_page,
                size=limit,
                pages=pages,
                items=[function_to_read(fn) for fn in functions]
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while fetching the functions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="error while fetching the functions."
        )
    


@router.get(
    "/{function_id}",
    response_model=FunctionRead,
    summary="Get function by ID",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_function_by_id(
    function_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        fn = (
            db.session.query(FunctionModel)
            .options(selectinload(FunctionModel.api_endpoint_url))
            .filter(FunctionModel.id == function_id)
            .first()
        )

        if not fn:
            logger.info("no function found")
            raise HTTPException(status_code=404, detail="Function not found")
        logger.info("function fetched successfully")
        return function_to_read(fn)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while fetching the function: {e}")
        raise HTTPException(
            status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail= "failed to fetch the function at the moment."
        )


@router.put(
    "/{function_id}",
    response_model=FunctionRead,
    summary="Update function",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_function(
    function_id: int,
    fn_in: FunctionUpdateSchema,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        fn = (
            db.session.query(FunctionModel)
            .filter(FunctionModel.id == function_id)
            .first()
        )

        if not fn:
            logger.info("function not found")
            raise HTTPException(status_code=404, detail="Function not found")

        # ---- Base Fields ----
        if fn_in.name is not None:
            fn.name = fn_in.name
        if fn_in.description is not None:
            fn.description = fn_in.description

        # ---- API Config (replace-all strategy for 1:1) ----
        if fn_in.api_config is not None:
            db.session.query(FunctionApiConfig).filter(
                FunctionApiConfig.function_id == function_id
            ).delete()

            cfg = fn_in.api_config
            db.session.add(
                FunctionApiConfig(
                    function_id=function_id,
                    endpoint_url=str(cfg.endpoint_url),
                    http_method=cfg.http_method,
                    timeout_ms=cfg.timeout_ms,
                    headers=cfg.headers,
                    query_params=cfg.query_params,
                    llm_response_schema=cfg.llm_response_schema,
                    response_variables=cfg.response_variables,
                )
            )

        db.session.commit()
        db.session.refresh(fn)
        logger.info("function updated successfully")
        return function_to_read(fn)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error while updating fucntion: {e}")
        raise HTTPException(
            status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to update function at the moment"
        )
    


@router.delete(
    "/{function_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete function",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
@router.delete(
    "/{function_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete function",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def delete_function(
    function_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        fn = (
            db.session.query(FunctionModel)
            .options(
                selectinload(FunctionModel.agent_functions).selectinload(
                    AgentFunctionBridgeModel.agent
                )
            )
            .filter(FunctionModel.id == function_id)
            .first()
        )

        if not fn:
            logger.info("no function found")
            raise HTTPException(status_code=404, detail="Function not found")

        # ---- ElevenLabs Cleanup ----
        if fn.elevenlabs_tool_id:
            try:
                client = ElevenLabsAgent()
                
                # 1. Detach from all linked agents
                for bridge in fn.agent_functions:
                    agent = bridge.agent
                    if agent and agent.elevenlabs_agent_id:
                        # Fetch current tools
                        tools_resp = client.get_agent_tools(agent.elevenlabs_agent_id)
                        if tools_resp.status:
                            current_tool_ids = tools_resp.data.get("tool_ids", [])
                            
                            if fn.elevenlabs_tool_id in current_tool_ids:
                                current_tool_ids.remove(fn.elevenlabs_tool_id)
                                
                                # Update agent to remove tool
                                update_resp = client.update_agent(
                                    agent_id=agent.elevenlabs_agent_id,
                                    tool_ids=current_tool_ids
                                )
                                if not update_resp.status:
                                    logger.error(f"Failed to detach tool from agent {agent.id}: {update_resp.error_message}")
                
                # 2. Delete the tool itself
                del_resp = client.delete_tool(fn.elevenlabs_tool_id)
                if not del_resp.status:
                     logger.warning(f"Failed to delete tool from ElevenLabs: {del_resp.error_message}")
                     # We proceed with DB deletion even if remote delete fails (it might be already gone)

            except Exception as e:
                logger.error(f"Error cleaning up ElevenLabs tool: {e}")
                # We typically want to proceed with DB delete to allow "force delete", 
                # but let's log it clearly. 

        db.session.delete(fn)
        db.session.commit()
        logger.info("function deleted successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"error whlie deleting the function: {e}")
        raise HTTPException(
            status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to delete function at the moment"
        )
