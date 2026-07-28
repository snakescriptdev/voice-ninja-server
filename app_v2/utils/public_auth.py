from urllib.parse import urlparse

from fastapi import Header, HTTPException, status, Depends, Request
from fastapi_sqlalchemy import db
from app_v2.databases.models import APIKeyModel, UnifiedAuthModel
from app_v2.utils.api_key_utils import verify_secret
from app_v2.utils.jwt_utils import get_client_ip
from datetime import datetime

def _origin_hostname(request: Request) -> str | None:
    """Best-effort caller hostname, from Origin then Referer."""
    header_value = request.headers.get("origin") or request.headers.get("referer")
    if not header_value:
        return None
    return (urlparse(header_value).hostname or header_value).lower()

async def get_public_api_user(
    request: Request,
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

        if api_key_record.allowed_ips:
            caller_ip = get_client_ip(request)
            if caller_ip not in api_key_record.allowed_ips:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Request IP address is not whitelisted for this API key",
                )

        if api_key_record.allowed_origins:
            caller_host = _origin_hostname(request)
            if not caller_host or caller_host not in api_key_record.allowed_origins:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Request origin is not whitelisted for this API key",
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
