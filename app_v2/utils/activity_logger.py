from typing import Dict, List, Optional

from fastapi_sqlalchemy import db
from app_v2.databases.models import ActivityLogModel, AgentModel, ConversationsModel
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


# Event types that represent an actually-completed conversation — coins are
# only ever deducted (in finalize_conversation, app_v2/utils/conversation_lifecycle.py)
# when a call/chat completes, so these are the only activity rows that should
# ever show a coins-used amount.
COIN_BEARING_ACTIVITY_EVENT_TYPES = {
    "agent_conversation_completed",
    "public_agent_conversation_completed",
    "widget_chat_ended",
}


def get_agent_ids_matching_name(name: str, user_id: Optional[int] = None) -> List[int]:
    """
    Agent ids whose name matches `name` (case-insensitive substring),
    optionally scoped to one user's own agents. ActivityLogModel has no
    agent_id column of its own (it's embedded in metadata_json), so
    filtering activity logs by agent name goes through this id lookup.
    """
    q = db.session.query(AgentModel.id).filter(AgentModel.agent_name.ilike(f"%{name}%"))
    if user_id is not None:
        q = q.filter(AgentModel.user_id == user_id)
    return [row.id for row in q.all()]


def enrich_activities_with_agent_and_coins(logs: List[ActivityLogModel]) -> Dict[int, Dict[str, object]]:
    """
    For a page of ActivityLogModel rows, batch-resolves:
    - agent_name: the live AgentModel.agent_name for whichever agent_id is
      embedded in metadata_json (falling back to the agent_name that was
      already snapshotted into metadata_json at log time, if the agent has
      since been deleted) — for any event type that references an agent.
    - coins: coins actually deducted for the call, looked up via the
      ElevenLabs conversation id embedded in metadata_json — only populated
      for COIN_BEARING_ACTIVITY_EVENT_TYPES, since coins are only ever
      consumed when a conversation is actually made.
    Returns {activity_log_id: {"agent_name": str | None, "coins": int | None}}.
    """
    agent_ids = set()
    conv_ids = set()
    for log in logs:
        meta = log.metadata_json or {}
        if meta.get("agent_id") is not None:
            agent_ids.add(meta["agent_id"])
        if log.event_type in COIN_BEARING_ACTIVITY_EVENT_TYPES and meta.get("conversation_id"):
            conv_ids.add(meta["conversation_id"])

    agent_names: Dict[int, str] = {}
    if agent_ids:
        rows = (
            db.session.query(AgentModel.id, AgentModel.agent_name)
            .filter(AgentModel.id.in_(agent_ids))
            .all()
        )
        agent_names = {row.id: row.agent_name for row in rows}

    coins_by_conv: Dict[str, int] = {}
    if conv_ids:
        rows = (
            db.session.query(ConversationsModel.elevenlabs_conv_id, ConversationsModel.coins_charged_to_user)
            .filter(ConversationsModel.elevenlabs_conv_id.in_(conv_ids))
            .all()
        )
        coins_by_conv = {row.elevenlabs_conv_id: row.coins_charged_to_user for row in rows}

    result: Dict[int, Dict[str, object]] = {}
    for log in logs:
        meta = log.metadata_json or {}
        agent_name = agent_names.get(meta.get("agent_id")) or meta.get("agent_name")
        coins = None
        if log.event_type in COIN_BEARING_ACTIVITY_EVENT_TYPES:
            coins = coins_by_conv.get(meta.get("conversation_id"))
        result[log.id] = {"agent_name": agent_name, "coins": coins}
    return result
