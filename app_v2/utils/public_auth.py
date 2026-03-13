from fastapi import Header, HTTPException, status, Depends
from fastapi_sqlalchemy import db
from app_v2.databases.models import APIKeyModel, UnifiedAuthModel
from app_v2.utils.api_key_utils import verify_secret
from datetime import datetime

async def get_public_api_user(
    x_api_client_id: str = Header(..., alias="X-API-Client-ID"),
    x_api_client_secret: str = Header(..., alias="X-API-Client-Secret")
) -> UnifiedAuthModel:
    """
    Dependency to authenticate public API requests using Client ID and Client Secret.
    """
    with db():
        api_key_record = db.session.query(APIKeyModel).filter(
            APIKeyModel.client_id == x_api_client_id,
            APIKeyModel.is_active == True
        ).first()

        if not api_key_record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive Client ID",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        if not verify_secret(x_api_client_secret, api_key_record.client_secret_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Client Secret",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        user = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.id == api_key_record.user_id
        ).first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User associated with this API key not found",
            )
        
        # Proactively update daily usage tracking here? 
        # Or better in a separate middleware/decorator to not block the main logic.
        # Let's keep it simple for now and just return the user.
        
        return user
