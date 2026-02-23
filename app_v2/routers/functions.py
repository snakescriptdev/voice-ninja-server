from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from typing import List
import math

from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import (
    FunctionModel,
    FunctionApiConfig,
    UnifiedAuthModel
)
from app_v2.schemas.function_schema import (
    FunctionCreateSchema,
    FunctionUpdateSchema,
    FunctionRead
)
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.core.logger import setup_logger
from app_v2.utils.elevenlabs import ElevenLabsAgent
from app_v2.utils.crypto_utils import encrypt_data

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/functions",
    tags=["functions"],
)

security = HTTPBearer()

# -------------------- CREATE --------------------

@router.post(
    "/",
    response_model=FunctionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create function (tool)",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def create_function(
    function_in: FunctionCreateSchema,
    current_user: UnifiedAuthModel = Depends(get_current_user),
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
                detail=f"Failed to create tool in ElevenLabs: {el_response.error_message}"
            )
        
        elevenlabs_tool_id = el_response.data.get("id")
        logger.info(f"✅ ElevenLabs tool created: {elevenlabs_tool_id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error creating ElevenLabs tool")
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=f"Unexpected error while creating ElevenLabs tool: {str(e)}"
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
        
        return FunctionRead.model_validate(new_function)
        
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
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    if page < 1:
        page = 1
    skip = (page - 1) * size
    
    query = db.session.query(FunctionModel).filter(
        FunctionModel.user_id == current_user.id
    ).order_by(FunctionModel.modified_at.desc())
    
    total = query.count()
    pages = math.ceil(total / size)
    
    functions = query.offset(skip).limit(size).all()
    
    items = [FunctionRead.model_validate(f) for f in functions]
    
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
    current_user: UnifiedAuthModel = Depends(get_current_user),
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
        
    return FunctionRead.model_validate(function)

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
    current_user: UnifiedAuthModel = Depends(get_current_user),
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

    # 1. Prepare ElevenLabs Update if needed
    el_update = False
    el_params = {}
    
    if function_in.name is not None:
        function.name = function_in.name
        el_params["name"] = function_in.name
        el_update = True
        
    if function_in.description is not None:
        function.description = function_in.description
        el_params["description"] = function_in.description
        el_update = True
        
    # Allow top-level response_variables update
    if function_in.response_variables is not None:
        if not function.api_endpoint_url:
            function.api_endpoint_url = FunctionApiConfig(function_id=function_id)
            db.session.add(function.api_endpoint_url)
        function.api_endpoint_url.response_variables = function_in.response_variables
        
    if function_in.api_config is not None:
        api_config = function.api_endpoint_url
        if not api_config:
            api_config = FunctionApiConfig(function_id=function_id)
            db.session.add(api_config)
            
        # Update fields only if provided
        if function_in.api_config.url:
            api_config.endpoint_url = function_in.api_config.url
        if function_in.api_config.method:
            api_config.http_method = function_in.api_config.method
            
        if function_in.api_config.request_headers is not None:
            headers = function_in.api_config.request_headers
            sensitive_keys = {"authorization", "x-api-key", "api-key", "token"}
            encrypted_headers = {}
            for k, v in headers.items():
                if k.lower() in sensitive_keys:
                    encrypted_headers[k] = encrypt_data(v)
                else:
                    encrypted_headers[k] = v
            api_config.headers = encrypted_headers

        if function_in.api_config.path_params_schema is not None:
            api_config.path_params = {k: v.model_dump(exclude_none=True) for k, v in function_in.api_config.path_params_schema.items()}
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

        # Merged data for ElevenLabs
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
        
        # Validate merged config via ApiSchema before sending to EL
        el_params["api_schema"] = ApiSchema(**api_config_data)
        el_update = True

    # 2. Sync with ElevenLabs
    if el_update and function.elevenlabs_tool_id:
        el_client = ElevenLabsAgent()
        try:
            logger.info(f"Updating ElevenLabs tool concurrently: {function.elevenlabs_tool_id}")
            el_response = el_client.update_tool(
                tool_id=function.elevenlabs_tool_id,
                **el_params
            )
            
            if not el_response.status:
                logger.error(f"❌ ElevenLabs tool update failed: {el_response.error_message}")
                # Optional: Decide if we should rollback DB or just warn
                # For consistency, let's rollback if name or description failed in EL
                db.session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY,
                    detail=f"Failed to update tool in ElevenLabs: {el_response.error_message}"
                )
            logger.info(f"✅ ElevenLabs tool '{function.elevenlabs_tool_id}' updated successfully")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during ElevenLabs tool update: {e}")
            db.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                detail=f"Failed to update tool in ElevenLabs due to an unexpected error: {str(e)}"
            )

    try:
        db.session.commit()
        db.session.refresh(function)
        return FunctionRead.model_validate(function)
        
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
    current_user: UnifiedAuthModel = Depends(get_current_user),
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
