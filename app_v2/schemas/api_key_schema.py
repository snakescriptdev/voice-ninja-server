from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime

def _clean_string_list(value: Optional[List[str]]) -> Optional[List[str]]:
    if value is None:
        return None
    cleaned = [item.strip() for item in value if item and item.strip()]
    return cleaned or None

class APIKeyCreate(BaseModel):
    name: Optional[str] = None
    allowed_ips: Optional[List[str]] = None
    allowed_origins: Optional[List[str]] = None

    @field_validator("allowed_ips", "allowed_origins")
    @classmethod
    def _drop_blank_entries(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        return _clean_string_list(value)

class APIKeyUpdate(BaseModel):
    """Partial update — only the whitelist fields are editable after creation.
    Sending `null` (or an empty list) clears that restriction; omitting a
    field leaves it unchanged.
    """
    allowed_ips: Optional[List[str]] = None
    allowed_origins: Optional[List[str]] = None

    @field_validator("allowed_ips", "allowed_origins")
    @classmethod
    def _drop_blank_entries(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        return _clean_string_list(value)

class APIKeyResponse(BaseModel):
    id: int
    name: Optional[str]
    client_id: str
    client_secret_last4: Optional[str] = None
    is_active: bool
    created_at: datetime
    allowed_ips: Optional[List[str]] = None
    allowed_origins: Optional[List[str]] = None

    class Config:
        from_attributes = True

class APIKeyFullResponse(APIKeyResponse):
    client_secret: str # Only returned once upon creation
