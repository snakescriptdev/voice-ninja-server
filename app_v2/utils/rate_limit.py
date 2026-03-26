from fastapi import HTTPException, status
from fastapi_sqlalchemy import db
from datetime import datetime, timezone, timedelta
from app_v2.databases.models import ActivityLogModel, APIDailyUsageModel, APICallLogModel
from sqlalchemy import func
from app_v2.utils.activity_logger import log_activity

from app_v2.utils.feature_access import get_feature_limit

RATE_LIMIT_RPM_DEFAULT = 60 # Default 60 requests per minute

def track_and_limit_api(user_id: int):
    """
    Track API usage and enforce rate limits.
    """
    now = datetime.now(timezone.utc)
    one_minute_ago = now - timedelta(minutes=1)
    today = now.date()

    # Fetch dynamic rate limit from plan
    plan_limit = get_feature_limit(user_id, "api_access")
    rate_limit_rpm = int(plan_limit) if plan_limit is not None else RATE_LIMIT_RPM_DEFAULT

    with db():
        # 1. Enforce Rate Limit (RPM)
        # Counting recent hits in activity_logs
        recent_hits = db.session.query(func.count(ActivityLogModel.id)).filter(
            ActivityLogModel.user_id == user_id,
            ActivityLogModel.event_type == "public_api_hit",
            ActivityLogModel.created_at >= one_minute_ago
        ).scalar()

        if recent_hits >= rate_limit_rpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {rate_limit_rpm} requests per minute."
            )

        # 2. Log activity
        log_activity(
            user_id=user_id,
            event_type="public_api_hit",
            description="Public API request received",
            metadata={"timestamp": now.isoformat()}
        )

        # 3. Increment Daily Usage
        today_dt = datetime(today.year, today.month, today.day)
        
        usage_record = db.session.query(APIDailyUsageModel).filter(
            APIDailyUsageModel.user_id == user_id,
            APIDailyUsageModel.usage_date == today_dt
        ).first()

        if usage_record:
            usage_record.hit_count += 1
        else:
            usage_record = APIDailyUsageModel(
                user_id=user_id,
                usage_date=today_dt,
                hit_count=1
            )
            db.session.add(usage_record)
        
        # We don't commit here if we want to include more work in the same session,
        # but the original code had db.session.commit().
        db.session.commit()

def log_public_api_call(user_id: int, api_route: str, status_code: int, response_time_ms: int, coins_used: int = 0):
    """
    Logs a detailed public API call record.
    """
    try:
        with db():
            log_entry = APICallLogModel(
                user_id=user_id,
                api_route=api_route,
                status_code=status_code,
                response_time_ms=response_time_ms,
                coins_used=coins_used
            )
            db.session.add(log_entry)
            db.session.commit()
    except Exception as e:
        # Avoid crashing the response if logging fails
        import logging
        logging.getLogger(__name__).error(f"Failed to log public API call: {e}")
