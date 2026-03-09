from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class APIAnalyticsResponse(BaseModel):
    total_api_calls_this_month: int
    coins_used_via_api_this_month: int
    avg_response_time_24h_ms: float

class APICallLogRead(BaseModel):
    id: int
    api_route: str
    status_code: int
    response_time_ms: Optional[int]
    coins_used: int
    created_at: datetime

    class Config:
        from_attributes = True
