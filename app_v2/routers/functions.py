from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_
from typing import List, Optional
import math

from app_v2.utils.jwt_utils import require_active_user, HTTPBearer
from app_v2.databases.models import (
    FunctionModel,
    FunctionApiConfig,
    UnifiedAuthModel,
    AgentModel,
    AgentFunctionBridgeModel,
)
from app_v2.schemas.function_schema import (
    FunctionCreateSchema,
    FunctionUpdateSchema,
    FunctionRead,
    FunctionAgentItem,
    ApiSchema,
    PrimitiveField
)
from app_v2.schemas.pagination import PaginatedResponse, PageSize
from app_v2.core.logger import setup_logger
from app_v2.utils.elevenlabs import ElevenLabsAgent
from app_v2.utils.crypto_utils import encrypt_data
from sqlalchemy.orm import joinedload
from app_v2.schemas.function_schema import HttpMethod

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/functions",
    tags=["functions"],
)

security = HTTPBearer()

# -------------------- CREATE --------------------

from app_v2.databases.models import FunctionModel
from app_v2.utils.crypto_utils import decrypt_data


def _get_agents_count(function_id: int) -> int:
    return (
        db.session.query(func.count(AgentFunctionBridgeModel.id))
        .filter(AgentFunctionBridgeModel.function_id == function_id)
        .scalar()
        or 0
    )


def function_to_read(f: FunctionModel, agents_count: int = 0) -> FunctionRead:

    db_config = f.api_endpoint_url

    decrypted_headers = {}

    if db_config and db_config.headers:
        sensitive_keys = {"authorization", "x-api-key", "api-key", "token"}

        for k, v in db_config.headers.items():
            if k.lower() in sensitive_keys:
                try:
                    decrypted_headers[k] = decrypt_data(v)
                except Exception:
                    decrypted_headers[k] = v
            else:
                decrypted_headers[k] = v

    api_schema = None

    if db_config:
        api_schema = ApiSchema(
            url=db_config.endpoint_url,
            method=db_config.http_method,
            request_headers=decrypted_headers,
            path_params_schema=(
                {k: PrimitiveField(**v) for k, v in db_config.path_params.items()}
                if db_config.path_params else None
            ),
            query_params_schema=db_config.query_params,
            request_body_schema=db_config.body_schema,
            response_variables=db_config.response_variables,
            content_type="application/json" if db_config.body_schema else None,
        )

    return FunctionRead(
        id=f.id,
        name=f.name,
        description=f.description,
        elevenlabs_tool_id=f.elevenlabs_tool_id,
        created_at=f.created_at,
        modified_at=f.modified_at,
        # System-managed tools (e.g. the auto-provisioned personal-KB search
        # tool) never expose their API config to the frontend — users can see
        # the tool exists but can't view/edit its underlying URL/headers/etc.
        api_config=api_schema if not f.is_system_managed else None,
        agents_count=agents_count,
        is_system_managed=f.is_system_managed,
    )

@router.post(
    "/",
    response_model=FunctionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create function (tool)",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def create_function(
    function_in: FunctionCreateSchema,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    user_id = current_user.id
    
    # Check for name uniqueness for the user
    existing = db.session.query(FunctionModel).filter(
        FunctionModel.name == function_in.name,
        FunctionModel.user_id == user_id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Function with name '{function_in.name}' already exists"
        )

    # 1. Create tool in ElevenLabs
    el_client = ElevenLabsAgent()
    try:
        logger.info(f"Creating ElevenLabs tool for function: {function_in.name}")
        el_response = el_client.create_tool(
            name=function_in.name,
            description=function_in.description,
            api_schema=function_in.api_config
        )
        
        if not el_response.status:
            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                detail=f"Failed to create tool: {el_response.error_message}"
            )
        
        elevenlabs_tool_id = el_response.data.get("id")
        logger.info(f"✅ ElevenLabs tool created: {elevenlabs_tool_id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error creating ElevenLabs tool")
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=f"Unexpected error while creating tool: {str(e)}"
        )

    # 2. Save to Database
    try:
        new_function = FunctionModel(
            name=function_in.name,
            description=function_in.description,
            user_id=user_id,
            elevenlabs_tool_id=elevenlabs_tool_id
        )
        db.session.add(new_function)
        db.session.flush()

        # Encrypt auth-related headers
        headers = function_in.api_config.request_headers or {}
        sensitive_keys = {"authorization", "x-api-key", "api-key", "token"}
        encrypted_headers = {}
        for k, v in headers.items():
            if k.lower() in sensitive_keys:
                encrypted_headers[k] = encrypt_data(v)
            else:
                encrypted_headers[k] = v

        api_config = FunctionApiConfig(
            function_id=new_function.id,
            endpoint_url=function_in.api_config.url,
            http_method=function_in.api_config.method,
            headers=encrypted_headers,
            path_params={k: v.model_dump(exclude_none=True) for k, v in function_in.api_config.path_params_schema.items()} if function_in.api_config.path_params_schema else None,
            query_params=function_in.api_config.query_params_schema.model_dump(exclude_none=True) if function_in.api_config.query_params_schema else None,
            body_schema=function_in.api_config.request_body_schema.model_dump() if function_in.api_config.request_body_schema else None,
            response_variables=function_in.api_config.response_variables,
            timeout_ms=30000, # Default timeout
            speak_while_execution=False,
            speak_after_execution=True
        )
        db.session.add(api_config)
        
        db.session.commit()
        db.session.refresh(new_function)
        
        return function_to_read(new_function)
        
    except Exception as db_error:
        db.session.rollback()
        # Cleanup ElevenLabs tool if DB fails
        if elevenlabs_tool_id:
            try:
                el_client.delete_tool(elevenlabs_tool_id)
                logger.info(f"Cleaned up orphan ElevenLabs tool: {elevenlabs_tool_id}")
            except Exception as cleanup_err:
                logger.warning(f"Failed to cleanup orphan ElevenLabs tool {elevenlabs_tool_id}: {cleanup_err}")
                
        logger.exception("Database error while creating function")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save function to database: {str(db_error)}"
        )

# -------------------- GET ALL --------------------

@router.get(
    "/",
    response_model=PaginatedResponse[FunctionRead],
    summary="Get all functions",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_all_functions(
    page: int = Query(1, ge=1),
    size: PageSize = 10,
    name: Optional[str] = None,
    method: Optional[str] = None,
    agent_name: Optional[str] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    if page < 1:
        page = 1
    skip = (page - 1) * size

    query = db.session.query(FunctionModel).filter(
        FunctionModel.user_id == current_user.id,
    ).options(joinedload(FunctionModel.api_endpoint_url))

    if name:
        query = query.filter(FunctionModel.name.ilike(f"%{name}%"))

    if method:
        query = query.filter(
            db.session.query(FunctionApiConfig.id)
            .filter(
                FunctionApiConfig.function_id == FunctionModel.id,
                FunctionApiConfig.http_method == method,
            )
            .exists()
        )

    if agent_name:
        query = query.filter(
            db.session.query(AgentFunctionBridgeModel.id)
            .join(AgentModel, AgentModel.id == AgentFunctionBridgeModel.agent_id)
            .filter(
                AgentFunctionBridgeModel.function_id == FunctionModel.id,
                AgentModel.user_id == current_user.id,
                AgentModel.agent_name.ilike(f"%{agent_name}%"),
            )
            .exists()
        )

    query = query.order_by(FunctionModel.modified_at.desc())

    total = query.count()
    pages = math.ceil(total / size)
    
    functions = query.offset(skip).limit(size).all()

    function_ids = [f.id for f in functions]
    counts_by_function_id = {}
    if function_ids:
        counts_by_function_id = dict(
            db.session.query(
                AgentFunctionBridgeModel.function_id,
                func.count(AgentFunctionBridgeModel.id),
            )
            .filter(AgentFunctionBridgeModel.function_id.in_(function_ids))
            .group_by(AgentFunctionBridgeModel.function_id)
            .all()
        )

    items = [
        function_to_read(f, agents_count=counts_by_function_id.get(f.id, 0))
        for f in functions
    ]
    
    return PaginatedResponse(
        total=total,
        page=page,
        size=size,
        pages=pages,
        items=items
    )

# -------------------- GET BY ID --------------------

@router.get(
    "/{function_id}",
    response_model=FunctionRead,
    summary="Get function by ID",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_function(
    function_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    function = db.session.query(FunctionModel).filter(
        FunctionModel.id == function_id,
        FunctionModel.user_id == current_user.id
    ).options(joinedload(FunctionModel.api_endpoint_url)).first()
    
    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Function not found"
        )

    return function_to_read(function, agents_count=_get_agents_count(function.id))

# -------------------- AGENTS USING THIS TOOL --------------------

@router.get(
    "/{function_id}/agents",
    response_model=PaginatedResponse[FunctionAgentItem],
    summary="List the current user's agents that have this tool attached, paginated",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_function_agents(
    function_id: int,
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    function = db.session.query(FunctionModel).filter(
        FunctionModel.id == function_id,
        or_(
            FunctionModel.user_id == current_user.id,
            FunctionModel.user_id.is_(None),
        ),
    ).first()
    if not function:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Function not found")

    if page < 1:
        page = 1

    # Scoped to the current user's OWN agents regardless of who owns the tool
    # (a shared/global tool must never leak other users' agent names).
    base = (
        db.session.query(AgentModel.id, AgentModel.agent_name)
        .join(AgentFunctionBridgeModel, AgentFunctionBridgeModel.agent_id == AgentModel.id)
        .filter(
            AgentFunctionBridgeModel.function_id == function_id,
            AgentModel.user_id == current_user.id,
        )
        .order_by(AgentModel.agent_name)
    )

    total = base.count()
    pages = math.ceil(total / size) if size > 0 else 1
    rows = base.offset((page - 1) * size).limit(size).all()
    items = [FunctionAgentItem(id=r.id, agent_name=r.agent_name) for r in rows]

    return PaginatedResponse(total=total, page=page, size=size, pages=pages, items=items)

# -------------------- UPDATE --------------------

@router.put(
    "/{function_id}",
    response_model=FunctionRead,
    summary="Update function",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_function(
    function_id: int,
    function_in: FunctionUpdateSchema,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    function = db.session.query(FunctionModel).filter(
        FunctionModel.id == function_id,
        FunctionModel.user_id == current_user.id
    ).first()

    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Function not found"
        )

    if function.is_system_managed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This tool is managed automatically and cannot be edited."
        )

    # 0. Name Uniqueness Check (if changed)
    if function_in.name != function.name:
        existing = db.session.query(FunctionModel).filter(
            FunctionModel.name == function_in.name,
            FunctionModel.user_id == current_user.id,
            FunctionModel.id != function_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Function with name '{function_in.name}' already exists"
            )

    # 1. Prepare ElevenLabs Update
    el_params = {}
    sensitive_keys = {"authorization", "x-api-key", "api-key", "token"}

    # name & description are required — assign directly
    function.name = function_in.name
    el_params["name"] = function_in.name

    function.description = function_in.description
    el_params["description"] = function_in.description

    # Allow top-level response_variables update (still optional)
    if function_in.response_variables is not None:
        if not function.api_endpoint_url:
            function.api_endpoint_url = FunctionApiConfig(function_id=function_id)
            db.session.add(function.api_endpoint_url)
        function.api_endpoint_url.response_variables = function_in.response_variables

    # api_config is required — no None check needed
    api_config = function.api_endpoint_url
    if not api_config:
        api_config = FunctionApiConfig(function_id=function_id)
        db.session.add(api_config)
        function.api_endpoint_url = api_config

    # url is required inside api_config — assign directly
    api_config.endpoint_url = function_in.api_config.url

    # All other api_config fields remain optional
    if function_in.api_config.method:
        api_config.http_method = function_in.api_config.method
        # Clear body_schema if method is changed to GET or DELETE as they don't support it
        if function_in.api_config.method in {HttpMethod.GET, HttpMethod.DELETE}:
            api_config.body_schema = None

    if function_in.api_config.request_headers is not None:
        headers = function_in.api_config.request_headers
        encrypted_headers = {}
        for k, v in headers.items():
            if k.lower() in sensitive_keys:
                encrypted_headers[k] = encrypt_data(v)
            else:
                encrypted_headers[k] = v
        api_config.headers = encrypted_headers

    if function_in.api_config.path_params_schema is not None:
        api_config.path_params = {
            k: v.model_dump(exclude_none=True)
            for k, v in function_in.api_config.path_params_schema.items()
        }
    if function_in.api_config.query_params_schema is not None:
        api_config.query_params = function_in.api_config.query_params_schema.model_dump(exclude_none=True)
    if function_in.api_config.request_body_schema is not None:
        api_config.body_schema = function_in.api_config.request_body_schema.model_dump()
    if function_in.api_config.response_variables is not None:
        api_config.response_variables = function_in.api_config.response_variables

    # Decrypt auth-related headers for ElevenLabs sync
    headers_to_sync = api_config.headers or {}
    decrypted_headers = {}
    for k, v in headers_to_sync.items():
        if k.lower() in sensitive_keys:
            try:
                decrypted_headers[k] = decrypt_data(v)
            except Exception:
                decrypted_headers[k] = v
        else:
            decrypted_headers[k] = v

    # Build merged config for ElevenLabs
    api_config_data = {
        "url": api_config.endpoint_url,
        "method": api_config.http_method,
        "request_headers": decrypted_headers,
        "path_params_schema": api_config.path_params,
        "query_params_schema": api_config.query_params,
        "request_body_schema": api_config.body_schema,
        "response_variables": api_config.response_variables,
        "content_type": "application/json" if api_config.body_schema else None,
    }

    try:
        el_params["api_schema"] = ApiSchema(**api_config_data)
    except Exception as ve:
        logger.error(f"Validation error building ApiSchema for ElevenLabs: {ve}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid configuration after merge: {str(ve)}"
        )

    # 2. Sync with ElevenLabs
    if function.elevenlabs_tool_id:
        el_client = ElevenLabsAgent()
        try:
            logger.info(f"Updating ElevenLabs tool: {function.elevenlabs_tool_id}")
            el_response = el_client.update_tool(
                tool_id=function.elevenlabs_tool_id,
                **el_params
            )

            if not el_response.status:
                logger.error(f"❌ ElevenLabs tool update failed: {el_response.error_message}")
                db.session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY,
                    detail=f"Failed to update tool: {el_response.error_message}"
                )
            logger.info(f"✅ ElevenLabs tool '{function.elevenlabs_tool_id}' updated successfully")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during ElevenLabs tool update: {e}")
            db.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                detail=f"Failed to update tool due to an unexpected error: {str(e)}"
            )

    try:
        db.session.commit()
        db.session.refresh(function)
        return function_to_read(function, agents_count=_get_agents_count(function.id))

    except Exception as e:
        db.session.rollback()
        logger.exception("Error updating function")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update function: {str(e)}"
        )

# -------------------- DELETE --------------------

@router.delete(
    "/{function_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete function",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def delete_function(
    function_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    function = db.session.query(FunctionModel).filter(
        FunctionModel.id == function_id,
        FunctionModel.user_id == current_user.id
    ).first()

    if not function:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Function not found"
        )

    if function.is_system_managed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This tool is managed automatically and cannot be deleted."
        )

    # This is a shared tool — deleting it detaches it from every agent that
    # has it attached, not just the one the user happened to be looking at.
    # Snapshot which agents are affected now, before the bridge rows cascade-delete.
    affected_agents = (
        db.session.query(AgentModel)
        .join(AgentFunctionBridgeModel, AgentFunctionBridgeModel.agent_id == AgentModel.id)
        .filter(AgentFunctionBridgeModel.function_id == function_id)
        .all()
    )

    # 1. Delete from ElevenLabs
    if function.elevenlabs_tool_id:
        el_client = ElevenLabsAgent()
        try:
            logger.info(f"Deleting ElevenLabs tool: {function.elevenlabs_tool_id}")
            el_response = el_client.delete_tool(function.elevenlabs_tool_id)
            if not el_response.status:
                logger.warning(f"Failed to delete ElevenLabs tool: {el_response.error_message}")
                # We often proceed even if EL delete fails to keep DB clean,
                # but let's be safe and let user know if it's a hard error.
        except Exception as e:
            logger.error(f"Error deleting ElevenLabs tool: {e}")

    # 2. Delete from Database
    try:
        db.session.delete(function)
        db.session.commit()
        logger.info(f"✅ Function deleted: {function_id}")
    except Exception as e:
        db.session.rollback()
        logger.exception("Error deleting function from database")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete function: {str(e)}"
        )

    # 3. Resync every other agent that had this tool attached, so their
    # ElevenLabs tool_ids list doesn't keep referencing the now-deleted tool.
    # Best-effort — the delete above already succeeded and must not be undone.
    from app_v2.routers.agents import _sync_agent_tool_ids_with_elevenlabs
    for agent in affected_agents:
        try:
            _sync_agent_tool_ids_with_elevenlabs(agent)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"Failed to resync agent {agent.id} after tool {function_id} deletion: {e}")
