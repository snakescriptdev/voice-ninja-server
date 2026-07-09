"""Pydantic schemas for profile-related endpoints."""

import re
from typing import Optional, Dict
from pydantic import BaseModel, Field, field_validator

# Columns for first_name/last_name/phone/address have no DB-enforced max length
# (plain String/VARCHAR without a length in the migrations), so these caps are
# app-level limits chosen to keep the data sane and the error messages friendly.
NAME_MAX_LENGTH = 50
ADDRESS_MAX_LENGTH = 255
PHONE_MIN_DIGITS = 10
PHONE_MAX_DIGITS = 15
# Letters, digits, spaces and common address punctuation.
ADDRESS_PATTERN = re.compile(r"^[\w\s,.'\-#/]+$", re.UNICODE)
PHONE_STRIP_CHARS_PATTERN = re.compile(r"[\s\-]")



class UserNotificationSchema(BaseModel):
    email_notifications: bool = Field(default=True)
    useage_alerts: bool = Field(default=True)
    expiry_alert: bool = Field(default=True) 


class UserNotificationUpdate(BaseModel):
    email_notifications: Optional[bool] =None
    useage_alerts: Optional[bool] =None
    expiry_alert: Optional[bool] =None



class UserNotificationRead(UserNotificationSchema):
    id : int

    class Config:
        from_attributes = True


class ProfileRequest(BaseModel):
    """Request schema for updating user profile.

    Attributes:
        first_name: User's first name.
        last_name: User's last name.
        phone: User's phone number.
        address: User's address.
        notification_settings: User's notification settings
    """

    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    phone: Optional[str] = Field(None, description='User phone number')
    address: Optional[str] = Field(None, description='User address')
    notification_settings: Optional[UserNotificationUpdate] = Field(None, description="User notification settings")

    @field_validator('first_name', 'last_name')
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        """Validate name fields: strip whitespace, check length, and ensure only alphabetic characters."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError('cannot be empty or only spaces')
        if len(v) < 2:
            raise ValueError('must be at least 2 characters long')
        if len(v) > NAME_MAX_LENGTH:
            raise ValueError(f'must not exceed {NAME_MAX_LENGTH} characters')
        if not v.replace(' ', '').replace('-', '').replace("'", '').isalpha():
            raise ValueError('must contain only letters, spaces, hyphens, and apostrophes')
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Validate phone: strip whitespace/hyphens, ensure only digits, and check length."""
        if v is None:
            return None
        if not v.strip():
            raise ValueError('cannot be empty or only spaces')
        cleaned = PHONE_STRIP_CHARS_PATTERN.sub('', v.strip())
        if not cleaned.isdigit():
            raise ValueError('must contain only digits (spaces and hyphens are allowed as separators)')
        if len(cleaned) < PHONE_MIN_DIGITS or len(cleaned) > PHONE_MAX_DIGITS:
            raise ValueError(f'must be between {PHONE_MIN_DIGITS} and {PHONE_MAX_DIGITS} digits long')
        return cleaned

    @field_validator('address')
    @classmethod
    def validate_address(cls, v: Optional[str]) -> Optional[str]:
        """Validate address: strip whitespace, check length, and restrict to safe characters."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError('cannot be empty or only spaces')
        if len(v) > ADDRESS_MAX_LENGTH:
            raise ValueError(f'must not exceed {ADDRESS_MAX_LENGTH} characters')
        if not ADDRESS_PATTERN.match(v):
            raise ValueError("can only contain letters, numbers, spaces, and , . ' - # / characters")
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
        notification_settings: User notification settings
        feature_limits: User plan limits
    """

    id: int = Field(..., description='User ID')
    email: Optional[str] = Field(None, description='User email address')
    phone: Optional[str] = Field(None, description='User phone number')
    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    address: Optional[str] = Field(None, description='User address')
    notification_settings: Optional[UserNotificationRead] = Field(None, description="User notification settings")
    is_new_user: bool = Field(False, description="Flag indicating if the user is new (first login session)")
    feature_limits: Optional[Dict[str, Optional[float]]] = Field(None, description="User plan limits")