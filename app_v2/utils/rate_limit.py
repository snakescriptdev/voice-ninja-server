from fastapi import HTTPException, status
from fastapi_sqlalchemy import db
from datetime import datetime, timedelta
from app_v2.databases.models import ActivityLogModel, APIDailyUsageModel
from sqlalchemy import func
from app_v2.utils.activity_logger import log_activity

RATE_LIMIT_RPM = 60 # 60 requests per minute

def track_and_limit_api(user_id: int):
    """
    Track API usage and enforce rate limits.
    """
    now = datetime.utcnow()
    one_minute_ago = now - timedelta(minutes=1)
    today = now.date()

    with db():
        # 1. Enforce Rate Limit (RPM)
        # Counting recent hits in activity_logs
        recent_hits = db.session.query(func.count(ActivityLogModel.id)).filter(
            ActivityLogModel.user_id == user_id,
            ActivityLogModel.event_type == "public_api_hit",
            ActivityLogModel.created_at >= one_minute_ago
        ).scalar()

        if recent_hits >= RATE_LIMIT_RPM:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {RATE_LIMIT_RPM} requests per minute."
            )

        # 2. Log activity
        # We don't want to use the standard log_activity here if it opens its own db() context 
        # that might conflict or be redundant, but activity_logger.py usually handles its own db context.
        # Let's direct log here to be safe within the same session if possible, 
        # or just use the utility if it's clean.
        log_activity(
            user_id=user_id,
            event_type="public_api_hit",
            description="Public API request received",
            metadata={"timestamp": now.isoformat()}
        )

        # 3. Increment Daily Usage
        # We need to find or create a record for today
        # Convert date to datetime for comparison as usage_date is DateTime in models.py
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
        
        db.session.commit()
