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
