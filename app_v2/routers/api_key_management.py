from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_sqlalchemy import db
from typing import List

from app_v2.databases.models import APIKeyModel, UnifiedAuthModel
from app_v2.schemas.api_key_schema import APIKeyCreate, APIKeyResponse, APIKeyFullResponse
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.utils.api_key_utils import generate_client_id, generate_client_secret, hash_secret
from sqlalchemy.exc import SQLAlchemyError
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

security = HTTPBearer()
router = APIRouter(
    prefix="/api/v2/api-keys",
    tags=["api-keys"],
    dependencies=[Depends(security)]
)

@router.post(
    "/",
    response_model=APIKeyFullResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    key_in: APIKeyCreate,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Generate a new API key for the user."""
    try:
        logger.info(f"Creating API key for user_id={current_user.id}")

        client_id = generate_client_id()
        client_secret = generate_client_secret()
        secret_hash = hash_secret(client_secret)

        with db():
            new_key = APIKeyModel(
                user_id=current_user.id,
                name=key_in.name,
                client_id=client_id,
                client_secret_hash=secret_hash
            )
            db.session.add(new_key)
            db.session.commit()
            db.session.refresh(new_key)

        logger.info(f"API key created successfully for user_id={current_user.id}, key_id={new_key.id}")

        response = APIKeyFullResponse(
            id=new_key.id,
            name=new_key.name,
            client_id=new_key.client_id,
            is_active=new_key.is_active,
            created_at=new_key.created_at,
            client_secret=client_secret
        )
        return response

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"Database error while creating API key for user_id={current_user.id}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create API key. Please try again later."
        )
    
    except HTTPException:
        raise

    except Exception as e:
        logger.error(
            f"Unexpected error while creating API key for user_id={current_user.id}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong while creating API key."
        )

@router.get(
    "/",
    response_model=List[APIKeyResponse],
)
async def list_api_keys(
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """List all API keys belonging to the current user."""
    try:
        logger.info(f"Fetching API keys for user_id={current_user.id}")

        with db():
            keys = db.session.query(APIKeyModel).filter(
                APIKeyModel.user_id == current_user.id
            ).all()

        logger.info(f"Found {len(keys)} API keys for user_id={current_user.id}")
        return keys

    except SQLAlchemyError as e:
        logger.error(
            f"Database error while listing API keys for user_id={current_user.id}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch API keys."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error while listing API keys for user_id={current_user.id}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong while fetching API keys."
        )

@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_api_key(
    key_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Revoke/Delete an API key."""
    try:
        logger.info(f"Deleting API key_id={key_id} for user_id={current_user.id}")

        with db():
            key = db.session.query(APIKeyModel).filter(
                APIKeyModel.id == key_id,
                APIKeyModel.user_id == current_user.id
            ).first()

            if not key:
                logger.warning(
                    f"API key not found. key_id={key_id}, user_id={current_user.id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="API key not found"
                )

            db.session.delete(key)
            db.session.commit()

        logger.info(f"API key deleted successfully. key_id={key_id}, user_id={current_user.id}")
        return None

    except HTTPException:
        raise

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"Database error while deleting key_id={key_id}, user_id={current_user.id}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete API key."
        )

    except Exception as e:
        logger.error(
            f"Unexpected error while deleting key_id={key_id}, user_id={current_user.id}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong while deleting API key."
        )