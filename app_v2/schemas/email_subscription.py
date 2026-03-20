from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from datetime import datetime


class EmailSubscribeRequest(BaseModel):
    email: EmailStr
    source: Optional[str] = "landing_page"


class EmailSubscribeResponse(BaseModel):
    message: str
    email: str


class EmailUnsubscribeResponse(BaseModel):
    message: str


# Admin
class EmailSubscriberAdminItem(BaseModel):
    id: int
    email: str
    source: Optional[str]
    is_active: bool
    subscribed_at: datetime
    unsubscribed_at: Optional[datetime]

    class Config:
        from_attributes = True
