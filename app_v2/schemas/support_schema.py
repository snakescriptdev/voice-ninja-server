from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime

from app_v2.schemas.enum_types import SupportTicketCategoryEnum, SupportTicketStatusEnum


class SupportTicketCreate(BaseModel):
    category: SupportTicketCategoryEnum
    subject: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=5000)

    @field_validator("subject")
    @classmethod
    def _validate_subject(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("Subject cannot be empty or only spaces.")
        return v

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("Message cannot be empty or only spaces.")
        return v


class SupportTicketRead(BaseModel):
    id: int
    category: SupportTicketCategoryEnum
    subject: str
    message: str
    status: SupportTicketStatusEnum
    admin_response: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SupportTicketAdminRead(SupportTicketRead):
    user_id: int
    user_email: Optional[str] = None
    user_name: Optional[str] = None


class SupportTicketListResponse(BaseModel):
    total: int
    page: int
    size: int
    pages: int
    tickets: List[SupportTicketRead]


class SupportTicketAdminListResponse(BaseModel):
    total: int
    page: int
    size: int
    pages: int
    tickets: List[SupportTicketAdminRead]


class SupportTicketAdminUpdate(BaseModel):
    status: Optional[SupportTicketStatusEnum] = None
    admin_response: Optional[str] = None

    @field_validator("admin_response")
    @classmethod
    def _validate_admin_response(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        v = value.strip()
        if not v:
            raise ValueError("Admin response cannot be empty or only spaces.")
        return v
