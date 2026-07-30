from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Any

class ActivityLogResponse(BaseModel):
    id: int
    user_id: int
    event_type: str
    description: str
    metadata_json: Optional[dict] = None
    created_at: datetime
    user_name: Optional[str] = None # For admin view

    model_config = {"from_attributes": True}


class AdminActivityItem(BaseModel):
    """Activity log row for the admin-wide activity dashboard — every user's
    activity side by side, with the acting user's email/name attached."""
    id: int
    user_id: int
    user_name: str
    user_email: str
    agent_name: Optional[str] = None
    # Coins deducted for the call — only populated for activity rows that
    # represent an actually-completed conversation (coins are only ever
    # consumed when a conversation is made).
    coins: Optional[int] = None
    event_type: str
    description: str
    metadata_json: Optional[dict] = None
    created_at: datetime
    time_ago: str

    model_config = {"from_attributes": True}
