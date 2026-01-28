from fastapi import Depends, HTTPException, status
from app_v2.utils.jwt_utils import get_current_user
from app_v2.databases.models import UnifiedAuthModel

def is_admin(
    current_user: UnifiedAuthModel = Depends(get_current_user)
) -> UnifiedAuthModel:
    
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Admin access required",
                "status": "failed",
                "status_code": 403
            }
        )

    return current_user
