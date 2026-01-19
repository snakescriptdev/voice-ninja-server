"""Pydantic schemas for profile-related endpoints."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ProfileRequest(BaseModel):
    """Request schema for updating user profile.

    Attributes:
        first_name: User's first name.
        last_name: User's last name.
        phone: User's phone number.
        address: User's address.
    """

    first_name: Optional[str] = Field(None, description='User first name', min_length=2, max_length=50)
    last_name: Optional[str] = Field(None, description='User last name', min_length=2, max_length=50)
    phone: Optional[str] = Field(None, description='User phone number', min_length=10, max_length=15)
    address: Optional[str] = Field(None, description='User address', min_length=5, max_length=200)

    @field_validator('first_name', 'last_name')
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        """Validate name fields: strip whitespace and check length."""
        if v is not None:
            v = v.strip()
            if len(v) < 2:
                raise ValueError('Name must be at least 2 characters long')
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Validate phone: strip whitespace and ensure no letters."""
        if v is not None:
            v = v.strip()
            if not v.isdigit():
                raise ValueError('Phone number must contain only digits')
            if len(v) < 10:
                raise ValueError('Phone number must be at least 10 digits')
        return v

    @field_validator('address')
    @classmethod
    def validate_address(cls, v: Optional[str]) -> Optional[str]:
        """Validate address: strip whitespace."""
        if v is not None:
            v = v.strip()
        return v


class ProfileResponse(BaseModel):
    """Response schema for profile operations.

    Attributes:
        status: Response status (success or failed).
        status_code: HTTP status code.
        message: Response message.
        data: Response data containing profile information.
    """

    status: str = Field(..., description='Response status', examples=['success'])
    status_code: int = Field(..., description='HTTP status code', examples=[200])
    message: str = Field(..., description='Response message')
    data: dict = Field(
        default_factory=dict,
        description='Response data containing profile information'
    )


class ProfileInfo(BaseModel):
    """User profile information.

    Attributes:
        id: User ID.
        email: User email address.
        phone: User phone number.
        first_name: User first name.
        last_name: User last name.
        address: User address.
    """

    id: int = Field(..., description='User ID')
    email: Optional[str] = Field(None, description='User email address')
    phone: Optional[str] = Field(None, description='User phone number')
    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    address: Optional[str] = Field(None, description='User address')