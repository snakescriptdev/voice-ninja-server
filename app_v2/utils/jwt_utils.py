from jose import jwt, JWTError
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Header, Depends, Request, status
from fastapi.security import HTTPBearer as FastAPIHTTPBearer, HTTPAuthorizationCredentials
from fastapi.security.http import HTTPAuthorizationCredentials
import os
import re
from typing import Optional, Tuple

from fastapi_sqlalchemy import db

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, UserSessionModel

logger = setup_logger(__name__)


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
REFRESH_TOKEN_EXPIRE_DAYS = 7

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Create access token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: int, jti: str) -> str:
    """Create refresh token as JWT.

    `jti` is the session identifier minted once at login and shared with the
    matching access token / UserSessionModel row. It is NOT rotated when this
    refresh token is used to mint new access tokens - the same jti (and thus
    the same session row) persists for the life of the login.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {
        "user_id": user_id,
        "jti": jti,
        "exp": expire,
        "type": "refresh"
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_refresh_token(token: str) -> Tuple[Optional[int], Optional[str]]:
    """Verify refresh token and return (user_id, jti).

    Returns (None, None) if the token is invalid, expired, or not a refresh
    token. Callers must check `user_id is None` (not just falsiness) since a
    valid jti could theoretically be an empty string.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None, None
        return payload.get("user_id"), payload.get("jti")
    except JWTError:
        return None, None


# ---------------------------------------------------------------------------
# Session helpers (server-side session tracking / revocation)
# ---------------------------------------------------------------------------

def get_client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP, preferring the first X-Forwarded-For hop.

    This app has no existing precedent for reading X-Forwarded-For elsewhere
    in the codebase, so this is a new, self-contained convention: if the app
    runs behind a proxy/load balancer, `request.client.host` would otherwise
    just be the proxy's own address.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


def parse_device_label(user_agent: Optional[str]) -> Optional[str]:
    """Turn a raw User-Agent header into a short human-readable label.

    Deliberately a small substring/regex heuristic instead of a third-party
    UA-parsing dependency - "good enough" for a device list, not a precise
    UA parser. Produces things like "Chrome on Windows" or "Safari on iPhone".
    """
    if not user_agent:
        return None
    ua = user_agent

    if re.search(r"iPhone", ua, re.IGNORECASE):
        os_label = "iPhone"
    elif re.search(r"iPad", ua, re.IGNORECASE):
        os_label = "iPad"
    elif re.search(r"Android", ua, re.IGNORECASE):
        os_label = "Android"
    elif re.search(r"Mac OS X|Macintosh", ua, re.IGNORECASE):
        os_label = "Mac OS"
    elif re.search(r"Windows", ua, re.IGNORECASE):
        os_label = "Windows"
    elif re.search(r"Linux", ua, re.IGNORECASE):
        os_label = "Linux"
    else:
        os_label = "Unknown OS"

    # Order matters: Edge/Chrome UAs also contain "Safari", and Chrome UAs
    # also contain "Safari" but real Safari UAs don't contain "Chrome"/"Edg".
    if re.search(r"Edg/|EdgA/|EdgiOS/", ua):
        browser = "Edge"
    elif re.search(r"OPR/|Opera", ua):
        browser = "Opera"
    elif re.search(r"Chrome/|CriOS/", ua):
        browser = "Chrome"
    elif re.search(r"Firefox/|FxiOS/", ua):
        browser = "Firefox"
    elif re.search(r"Safari/", ua):
        browser = "Safari"
    else:
        browser = "Unknown browser"

    return f"{browser} on {os_label}"


def create_user_session(user_id: int, jti: str, request: Optional[Request]) -> Optional[UserSessionModel]:
    """Create a UserSessionModel row for a fresh login.

    Best-effort: if this fails for any reason we log it but do NOT raise,
    since we'd rather let the user log in without server-side session
    tracking than block login entirely on a bookkeeping row.
    """
    try:
        user_agent = request.headers.get("user-agent") if request else None
        ip_address = get_client_ip(request) if request else None
        return UserSessionModel.create(
            user_id=user_id,
            jti=jti,
            device_label=parse_device_label(user_agent),
            user_agent=user_agent,
            ip_address=ip_address,
        )
    except Exception as e:
        logger.error(f"Failed to create user session for user_id={user_id}: {e}", exc_info=True)
        return None


def revoke_session_by_jti(user_id: int, jti: str) -> bool:
    """Revoke a single session by jti. Returns True if a row was revoked."""
    try:
        with db():
            session_row = (
                db.session.query(UserSessionModel)
                .filter(UserSessionModel.jti == jti, UserSessionModel.user_id == user_id)
                .first()
            )
            if not session_row or session_row.is_revoked:
                return False
            session_row.is_revoked = True
            session_row.revoked_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
    except Exception as e:
        logger.error(f"Failed to revoke session jti={jti} for user_id={user_id}: {e}", exc_info=True)
        return False


def revoke_all_sessions(user_id: int, exclude_jti: Optional[str] = None) -> int:
    """Revoke all of a user's active sessions, optionally excluding one jti
    (used for "log out all OTHER devices"). Returns the number revoked.
    """
    try:
        with db():
            query = db.session.query(UserSessionModel).filter(
                UserSessionModel.user_id == user_id,
                UserSessionModel.is_revoked == False,  # noqa: E712
            )
            if exclude_jti:
                query = query.filter(UserSessionModel.jti != exclude_jti)
            rows = query.all()
            now = datetime.now(timezone.utc)
            for row in rows:
                row.is_revoked = True
                row.revoked_at = now
            db.session.commit()
            return len(rows)
    except Exception as e:
        logger.error(f"Failed to revoke all sessions for user_id={user_id}: {e}", exc_info=True)
        return 0

# Security scheme
security = HTTPBearer()

def _decode_access_token_str(token: str) -> UnifiedAuthModel:
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

        # Server-side revocation check. This runs on EVERY authenticated
        # request (one extra DB query in the hot auth path) so that revoking
        # a session takes effect immediately on the very next request,
        # instead of only at the next token refresh.
        jti = payload.get("jti")
        session_row = None
        if not jti:
            # Access token predates session tracking (issued before this
            # deploy) - there is no session row to check against, so treat
            # it the same as a revoked/unknown session and force re-login.
            raise HTTPException(
                status_code=401,
                detail={
                    "message": "Session has been revoked or does not exist",
                    "status": "failed",
                    "status_code": 401
                }
            )

        # The existence/revocation lookup itself is NOT best-effort - it's
        # the actual security check, so a failure here should surface (same
        # as the UnifiedAuthModel lookup above would). Only the last_used_at
        # bump afterwards is best-effort bookkeeping.
        with db():
            session_row = (
                db.session.query(UserSessionModel)
                .filter(UserSessionModel.jti == jti)
                .first()
            )

        if not session_row or session_row.is_revoked:
            raise HTTPException(
                status_code=401,
                detail={
                    "message": "Session has been revoked or does not exist",
                    "status": "failed",
                    "status_code": 401
                }
            )

        # Best-effort last_used_at bump - a failure here must NOT break auth.
        try:
            with db():
                db.session.query(UserSessionModel).filter(
                    UserSessionModel.id == session_row.id
                ).update({"last_used_at": datetime.now(timezone.utc)})
                db.session.commit()
        except Exception as e:
            logger.error(f"Failed to bump last_used_at for jti={jti}: {e}", exc_info=True)

        # Stash the current session's jti on the (transient, non-persisted)
        # user object so downstream code - e.g. GET /auth/sessions and
        # /auth/logout - can identify "this" session without re-decoding the
        # bearer token a second time.
        user._current_jti = jti

        return user
    except HTTPException:
        raise
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Invalid or expired token",
                "status": "failed",
                "status_code": 401
            }
        )


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current user from token"""
    return _decode_access_token_str(credentials.credentials)


def is_admin(
    current_user: UnifiedAuthModel = Depends(get_current_user),
) -> UnifiedAuthModel:
    """
    Ensure the current user is an admin.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Admin access required",
                "status": "failed",
                "status_code": 403,
            },
        )

    return current_user

def require_active_user(allow_suspended:bool = False):
    def dependency(current_user: UnifiedAuthModel= Depends(get_current_user)):
        if not allow_suspended and current_user.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account suspended"
            )
        return current_user
    return dependency