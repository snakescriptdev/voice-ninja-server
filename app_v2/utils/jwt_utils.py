from jose import jwt, JWTError
from datetime import datetime, timedelta
from fastapi import HTTPException, Header, Depends, Request
from fastapi.security import HTTPBearer as FastAPIHTTPBearer, HTTPAuthorizationCredentials
from fastapi.security.http import HTTPAuthorizationCredentials
import os

from app_v2.core.config import VoiceSettings


class HTTPBearer(FastAPIHTTPBearer):
    """Custom HTTPBearer that returns structured error responses."""
    
    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials:
        try:
            return await super().__call__(request)
        except HTTPException as e:
            # Convert the default "Not authenticated" error to structured format
            raise HTTPException(
                status_code=401,
                detail={
                    "message": "Not authenticated",
                    "status": "failed",
                    "status_code": 401
                }
            )

SECRET_KEY = VoiceSettings.SECRET_KEY
ALGORITHM = VoiceSettings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = VoiceSettings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = 30  # 30 days

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Create access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: int) -> str:
    """Create refresh token as JWT"""
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {
        "user_id": user_id,
        "exp": expire,
        "type": "refresh"
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_refresh_token(token: str) -> int:
    """Verify refresh token and return user_id"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload.get("user_id")
    except JWTError:
        return None

def revoke_refresh_token(token: str):
    """Revoke a refresh token (placeholder - JWT tokens can't be revoked without blacklist)"""
    # In production, you'd store revoked tokens in Redis/database
    pass

# Security scheme
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current user from token"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=401,
                detail={
                    "message": "Invalid token type",
                    "status": "failed",
                    "status_code": 401
                }
            )
        
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail={
                    "message": "Invalid token",
                    "status": "failed",
                    "status_code": 401
                }
            )
        
        from app_v2.databases.models import UnifiedAuthModel
        user = UnifiedAuthModel.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=401,
                detail={
                    "message": "User not found",
                    "status": "failed",
                    "status_code": 401
                }
            )
        
        return user
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Invalid or expired token",
                "status": "failed",
                "status_code": 401
            }
        )
