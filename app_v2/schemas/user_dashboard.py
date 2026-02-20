from pydantic import BaseModel
from typing import List, Optional


class UserDashboardAgentResponse(BaseModel):
    id: int
    agent_name: str
    is_enabled: bool


class UserDashboardPhoneNumberResponse(BaseModel):
    id: int
    phone_number: str