from fastapi_sqlalchemy import db
from app_v2.databases.models import ActivityLogModel
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

def log_activity(user_id: int, event_type: str, description: str, metadata: dict = None):
    """
    Logs a user activity to the database.
    """
    try:
        activity = ActivityLogModel(
            user_id=user_id,
            event_type=event_type,
            description=description,
            metadata_json=metadata
        )
        db.session.add(activity)
        db.session.commit()
        logger.info(f"Activity logged: {event_type} for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to log activity {event_type} for user {user_id}: {e}")
        # We don't want to raise an exception here to avoid breaking the main flow
        # if logging fails, but we do log the error.
        db.session.rollback()
