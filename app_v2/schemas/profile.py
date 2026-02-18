"""Pydantic schemas for profile-related endpoints."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator



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
        if v is not None and v.strip():  # Only validate if not None and not empty
            v = v.strip()
            if len(v) < 2:
                raise ValueError('Name must be at least 2 characters long')
            if len(v) > 50:
                raise ValueError('Name must not exceed 50 characters')
            if not v.replace(' ', '').replace('-', '').replace("'", '').isalpha():
                raise ValueError('Name must contain only letters, spaces, hyphens, and apostrophes')
            return v
        return None if not v or not v.strip() else v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Validate phone: strip whitespace, ensure only digits, and must be exactly 10 digits."""
        if v is not None and v.strip():  # Only validate if not None and not empty
            v = v.strip()
            if not v.isdigit():
                raise ValueError('Phone number must contain only digits')
            if len(v) != 10:
                raise ValueError('Phone number must be exactly 10 digits')
            return v
        return None if not v or not v.strip() else v

    @field_validator('address')
    @classmethod
    def validate_address(cls, v: Optional[str]) -> Optional[str]:
        """Validate address: strip whitespace."""
        if v is not None and v.strip():
            return v.strip()
        return None if not v or not v.strip() else v


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
    """

    id: int = Field(..., description='User ID')
    email: Optional[str] = Field(None, description='User email address')
    phone: Optional[str] = Field(None, description='User phone number')
    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    address: Optional[str] = Field(None, description='User address')
    notification_settings: Optional[UserNotificationRead] = Field(None, description="User notification settings")
    is_new_user: bool = Field(False, description="Flag indicating if the user is new (first login session)")