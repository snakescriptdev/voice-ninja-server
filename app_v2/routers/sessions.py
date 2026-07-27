"""Server-side session management endpoints.

Lets a logged-in user see their active sessions (one per device/login) and
revoke them - either a single session or all of them ("log out all
devices"). This is the real, server-side counterpart to what used to be a
100% client-side (localStorage-clearing) logout.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app_v2.core.logger import setup_logger
from app_v2.databases.models import UnifiedAuthModel, UserSessionModel
from app_v2.utils.jwt_utils import require_active_user, revoke_session_by_jti, revoke_all_sessions
from app_v2.constants import STATUS_SUCCESS, STATUS_FAILED, HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR

logger = setup_logger(__name__)

router = APIRouter(prefix="/api/v2/auth/sessions", tags=["Sessions"])


class RevokeAllSessionsRequest(BaseModel):
    """Request schema for revoking all sessions.

    Attributes:
        include_current: If True, revoke every session including the one
            used to call this endpoint (full "logout everywhere"). If
            False, revoke every OTHER session but leave the current one
            active ("log out all other devices").
    """
    include_current: bool = Field(
        default=False,
        description="Also revoke the session making this request, not just the other ones"
    )


def _session_to_dict(session_row: UserSessionModel, current_jti: str | None) -> dict:
    return {
        "id": session_row.id,
        "device_label": session_row.device_label,
        "ip_address": session_row.ip_address,
        "created_at": session_row.created_at,
        "last_used_at": session_row.last_used_at,
        "is_current": bool(current_jti) and session_row.jti == current_jti,
    }


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="List active sessions",
    description="List the current user's active (non-revoked) sessions/devices, newest-used first.",
)
async def list_sessions(
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Return all active sessions for the current user."""
    try:
        current_jti = getattr(current_user, "_current_jti", None)
        rows = UserSessionModel.list_active_for_user(current_user.id)
        sessions = [_session_to_dict(row, current_jti) for row in rows]
        return {"sessions": sessions}
    except Exception as e:
        logger.error(f"Error listing sessions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "Failed to list sessions"
            }
        )


@router.post(
    "/{session_id}/revoke",
    status_code=status.HTTP_200_OK,
    summary="Revoke a session",
    description="Revoke one specific session belonging to the current user. Allowed even for the session making this request.",
)
async def revoke_session(
    session_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Revoke a single session by id. Must belong to the current user."""
    try:
        session_row = UserSessionModel.get_by_id_for_user(session_id, current_user.id)
        if not session_row:
            # Don't leak whether the id exists for another user.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": status.HTTP_404_NOT_FOUND,
                    "message": "Session not found"
                }
            )

        revoke_session_by_jti(current_user.id, session_row.jti)

        return {"message": "Session revoked"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking session {session_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "Failed to revoke session"
            }
        )


@router.post(
    "/revoke-all",
    status_code=status.HTTP_200_OK,
    summary="Revoke all sessions",
    description="Revoke all of the current user's sessions. `include_current=False` logs out every OTHER device; `include_current=True` logs out everywhere including this request.",
)
async def revoke_all_sessions_endpoint(
    request: RevokeAllSessionsRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Revoke all sessions for the current user."""
    try:
        current_jti = getattr(current_user, "_current_jti", None)
        exclude_jti = None if request.include_current else current_jti
        revoke_all_sessions(current_user.id, exclude_jti=exclude_jti)

        message = "All sessions revoked" if request.include_current else "All other sessions revoked"
        return {"message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking all sessions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "Failed to revoke sessions"
            }
        )
