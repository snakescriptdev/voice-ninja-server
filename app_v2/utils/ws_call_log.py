"""
Websocket-call-lifecycle logging into APICallLogModel, mirroring the
start_conversation/finalize_conversation/mark_conversation_failed pattern in
conversation_lifecycle.py: open a row when a public websocket connection is
first attributable to a user, then finalize it once the connection ends.

Unlike log_public_api_call (a single fire-and-forget insert per HTTP call),
a websocket call spans start -> (maybe long-lived) -> end, so it needs a
row opened up front and updated in place at the end.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi_sqlalchemy import db

from app_v2.databases.models import APICallLogModel
from app_v2.schemas.enum_types import PublicLogChannelEnum
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


def start_ws_call_log(
    user_id: int,
    channel: PublicLogChannelEnum,
    api_route: str,
    request_params: Optional[dict] = None,
    request_body: Optional[dict] = None,
    method: str = "WS",
    api_key_id: Optional[int] = None,
) -> Optional[int]:
    """
    Inserts an in-flight log row and returns its id, or None on failure.
    Never raises — a logging failure must not break the call. Must be
    called inside db().
    """
    try:
        log_entry = APICallLogModel(
            user_id=user_id,
            api_route=api_route,
            status_code=0,
            channel=channel,
            method=method,
            request_params=request_params,
            request_body=request_body,
            is_success=None,
            api_key_id=api_key_id,
        )
        db.session.add(log_entry)
        db.session.commit()
        db.session.refresh(log_entry)
        return log_entry.id
    except Exception as e:
        logger.error(f"Failed to start ws call log: {e}")
        return None


def finalize_ws_call_log(
    log_id: Optional[int],
    *,
    is_success: bool,
    status_code: int = 200,
    response_body: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> None:
    """
    Updates the row opened by start_ws_call_log() with the call's outcome.
    No-op if log_id is None (e.g. the call was rejected before a user could
    be resolved). Never raises. Must be called inside db().
    """
    if not log_id:
        return
    try:
        record = db.session.query(APICallLogModel).get(log_id)
        if record is None:
            return
        record.status_code = status_code
        record.is_success = is_success
        record.response_body = response_body
        record.error_message = error_message
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        record.response_time_ms = int(
            (datetime.now(timezone.utc) - created_at).total_seconds() * 1000
        )
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to finalize ws call log {log_id}: {e}")
