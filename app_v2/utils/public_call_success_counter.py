"""
Read/write helpers for PublicCallSuccessCounterModel — the lightweight
"successful calls" counter that stands in for full per-call log rows
(successes are intentionally never persisted as full APICallLogModel rows,
see log_public_api_call/finalize_ws_call_log). All read helpers here mirror
the filter/group shapes the public-logs aggregate endpoints already use
against APICallLogModel for failures, so the two sides combine correctly.

Must be called inside an active db() session block. increment_success_counter
never raises — a counting failure must not break the request/connection it's
counting.
"""
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi_sqlalchemy import db
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app_v2.databases.models import PublicCallSuccessCounterModel
from app_v2.schemas.enum_types import PublicLogChannelEnum
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


def increment_success_counter(
    user_id: Optional[int],
    channel: PublicLogChannelEnum,
    api_route: str,
    method: Optional[str] = None,
    api_key_id: Optional[int] = None,
) -> None:
    if user_id is None:
        # No user to attribute the success to (e.g. auth didn't resolve) —
        # nothing meaningful to count against.
        return
    try:
        today = datetime.now(timezone.utc).date()
        channel_value = channel.value if hasattr(channel, "value") else channel
        method_value = (method or "UNKNOWN").upper()
        stmt = pg_insert(PublicCallSuccessCounterModel).values(
            user_id=user_id,
            channel=channel_value,
            api_route=api_route,
            method=method_value,
            api_key_id=api_key_id or 0,
            call_date=today,
            success_count=1,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_public_call_success_counter",
            set_={"success_count": PublicCallSuccessCounterModel.success_count + 1},
        )
        db.session.execute(stmt)
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to increment public call success counter: {e}")


def _base_query(user_id: int, channels: List[PublicLogChannelEnum], api_key_id: Optional[int] = None):
    channel_values = [c.value if hasattr(c, "value") else c for c in channels]
    q = db.session.query(PublicCallSuccessCounterModel).filter(
        PublicCallSuccessCounterModel.user_id == user_id,
        PublicCallSuccessCounterModel.channel.in_(channel_values),
    )
    if api_key_id is not None:
        q = q.filter(PublicCallSuccessCounterModel.api_key_id == api_key_id)
    return q


def get_success_counts_by_endpoint_admin(
    channels: List[PublicLogChannelEnum],
) -> Dict[Tuple[str, str, str], int]:
    """Same as get_success_counts_by_endpoint but admin-wide (no user_id
    filter) — mirrors list_public_log_endpoints_for_admin's all-user query."""
    channel_values = [c.value if hasattr(c, "value") else c for c in channels]
    rows = (
        db.session.query(
            PublicCallSuccessCounterModel.channel,
            PublicCallSuccessCounterModel.api_route,
            PublicCallSuccessCounterModel.method,
            func.sum(PublicCallSuccessCounterModel.success_count).label("cnt"),
        )
        .filter(PublicCallSuccessCounterModel.channel.in_(channel_values))
        .group_by(
            PublicCallSuccessCounterModel.channel,
            PublicCallSuccessCounterModel.api_route,
            PublicCallSuccessCounterModel.method,
        )
        .all()
    )
    return {(r.channel, r.api_route, r.method): int(r.cnt or 0) for r in rows}


def get_success_counts_by_endpoint(
    user_id: int,
    channels: List[PublicLogChannelEnum],
    api_key_id: Optional[int] = None,
) -> Dict[Tuple[str, str, str], int]:
    """All-time success counts grouped by (channel, route, method) — mirrors
    the failure-count grouping in get_public_log_endpoints."""
    rows = (
        _base_query(user_id, channels, api_key_id)
        .with_entities(
            PublicCallSuccessCounterModel.channel,
            PublicCallSuccessCounterModel.api_route,
            PublicCallSuccessCounterModel.method,
            func.sum(PublicCallSuccessCounterModel.success_count).label("cnt"),
        )
        .group_by(
            PublicCallSuccessCounterModel.channel,
            PublicCallSuccessCounterModel.api_route,
            PublicCallSuccessCounterModel.method,
        )
        .all()
    )
    return {(r.channel, r.api_route, r.method): int(r.cnt or 0) for r in rows}


def get_success_count_total(
    user_id: int,
    channels: List[PublicLogChannelEnum],
    api_key_id: Optional[int] = None,
) -> int:
    """All-time total success count — mirrors get_public_log_overview."""
    total = (
        _base_query(user_id, channels, api_key_id)
        .with_entities(func.sum(PublicCallSuccessCounterModel.success_count))
        .scalar()
    )
    return int(total or 0)


def get_success_counts_by_day(
    user_id: int,
    channels: List[PublicLogChannelEnum],
    year: int,
    month: int,
) -> Dict[int, int]:
    """Day-of-month success counts — mirrors _day_of_month_graph's failure side."""
    month_start = date(year, month, 1)
    month_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    rows = (
        _base_query(user_id, channels)
        .filter(
            PublicCallSuccessCounterModel.call_date >= month_start,
            PublicCallSuccessCounterModel.call_date < month_end,
        )
        .with_entities(
            PublicCallSuccessCounterModel.call_date,
            func.sum(PublicCallSuccessCounterModel.success_count).label("cnt"),
        )
        .group_by(PublicCallSuccessCounterModel.call_date)
        .all()
    )
    return {r.call_date.day: int(r.cnt or 0) for r in rows}
