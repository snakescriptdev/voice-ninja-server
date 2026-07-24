"""Pydantic schemas for profile-related endpoints."""

import re
from typing import Optional, Dict
from pydantic import BaseModel, Field, field_validator

# Columns for first_name/last_name/phone/address have no DB-enforced max length
# (plain String/VARCHAR without a length in the migrations), so these caps are
# app-level limits chosen to keep the data sane and the error messages friendly.
NAME_MAX_LENGTH = 50
ADDRESS_MAX_LENGTH = 255
# Phone numbers must be exactly 10 digits - no more, no less, and no leading
# '+'/country code.
PHONE_DIGITS_LENGTH = 10
PHONE_REGEX = re.compile(r"^\d{10}$")
# Letters, digits, spaces and common address punctuation.
ADDRESS_PATTERN = re.compile(r"^[\w\s,.'\-#/]+$", re.UNICODE)
# Address must contain at least one letter - digits alone (e.g. a bare
# postal/PIN code) aren't a valid address; numbers are allowed but optional.
ADDRESS_HAS_LETTER_PATTERN = re.compile(r"[^\W\d_]", re.UNICODE)
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
        """Validate name fields: strip whitespace, check length, and ensure only alphabetic characters.

        These fields are optional (last_name always; first_name is only
        conditionally required, which is enforced in the router, not here).
        A blank/whitespace-only string is treated the same as not having
        provided the field at all - it must NOT block the rest of the
        request, it just means "no value". Format/length checks only run
        when a non-empty value is actually supplied.
        """
        if v is None:
            return None
        v = v.strip()
        if not v:
            # Optional field left blank - not an error.
            return None
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
        """Validate phone: optional field, but when a value IS provided it must
        be exactly 10 digits (spaces/hyphens used as separators are stripped
        before checking; no country code / '+' prefix is accepted)."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            # Optional field left blank - not an error.
            return None
        cleaned = PHONE_STRIP_CHARS_PATTERN.sub('', v)
        if not PHONE_REGEX.match(cleaned):
            raise ValueError(f'must be exactly {PHONE_DIGITS_LENGTH} digits')
        return cleaned

    @field_validator('address')
    @classmethod
    def validate_address(cls, v: Optional[str]) -> Optional[str]:
        """Validate address: strip whitespace, check length, and restrict to safe characters."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            # Optional field left blank - not an error.
            return None
        if len(v) > ADDRESS_MAX_LENGTH:
            raise ValueError(f'must not exceed {ADDRESS_MAX_LENGTH} characters')
        if not ADDRESS_PATTERN.match(v):
            raise ValueError("can only contain letters, numbers, spaces, and , . ' - # / characters")
        if not ADDRESS_HAS_LETTER_PATTERN.search(v):
            raise ValueError('must contain letters (numbers alone are not a valid address)')
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