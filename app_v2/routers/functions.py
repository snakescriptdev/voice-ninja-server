from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from app_v2.schemas.function_schema import (
    FunctionCreateSchema,
    FunctionRead,
    FunctionUpdateSchema
)
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
        api_configs=function.api_endpoint_url,
        created_at=function.created_at,
        modified_at=function.modified_at,
    )

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
        fn = FunctionModel(
            name=fn_in.name,
            description=fn_in.description,
        )
        db.session.add(fn)
        db.session.flush()  # <-- UNIQUE constraint is usually triggered here

        for cfg in fn_in.api_configs:
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

        # ---- Agent Binding ----
        if fn_in.agent_id:
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

        logger.info(f"Function created successfully | function_id={fn.id}")
        return function_to_read(fn)

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
            detail="Failed to create function"
        )

@router.get(
    "/",
    response_model=list[FunctionRead],
    summary="Get all functions",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_all_functions(
    agent_id: int | None = None,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
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
            bridges = (
                db.session.query(AgentFunctionBridgeModel)
                .options(
                    selectinload(AgentFunctionBridgeModel.function).selectinload(
                        FunctionModel.api_endpoint_url
                    )
                )
                .filter(AgentFunctionBridgeModel.agent_id == agent_id)
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
            
            return result

        else:
            functions = (
                db.session.query(FunctionModel)
                .options(selectinload(FunctionModel.api_endpoint_url))
                .all()
            )
            if not functions:
                return []
            
            return [function_to_read(fn) for fn in functions]

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

        # ---- API Configs (replace-all strategy) ----
        if fn_in.api_configs is not None:
            db.session.query(FunctionApiConfig).filter(
                FunctionApiConfig.function_id == function_id
            ).delete()

            for cfg in fn_in.api_configs:
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
async def delete_function(
    function_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    try:
        fn = db.session.query(FunctionModel).filter(
            FunctionModel.id == function_id
        ).first()

        if not fn:
            logger.info("no function found")
            raise HTTPException(status_code=404, detail="Function not found")

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
