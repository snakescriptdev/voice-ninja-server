"""Pydantic schemas for OTP-related endpoints."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class RequestOTPRequest(BaseModel):
    """Request schema for requesting OTP.

    Attributes:
        username: Email address or phone number for OTP delivery.
    """

    username: str = Field(
        ...,
        description='Email address or phone number',
        min_length=1,
        examples=['user@example.com', '+1234567890']
    )

    @field_validator('username')
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Strip whitespace from username."""
        return v.strip()


class OTPMethodInfo(BaseModel):
    """Information about OTP delivery method.

    Attributes:
        method: Delivery method (email or SMS).
    """

    method: str = Field(
        ...,
        description='OTP delivery method',
        examples=['email', 'SMS']
    )


class RequestOTPResponse(BaseModel):
    """Response schema for OTP request.

    Attributes:
        status: Response status (success or failed).
        status_code: HTTP status code.
        message: Response message.
        data: Additional response data containing method information.
    """

    status: str = Field(..., description='Response status', examples=['success'])
    status_code: int = Field(..., description='HTTP status code', examples=[200])
    message: str = Field(..., description='Response message')
    data: dict = Field(
        default_factory=dict,
        description='Additional response data'
    )


class VerifyOTPRequest(BaseModel):
    """Request schema for verifying OTP.

    Attributes:
        username: Email address or phone number used to request OTP.
        otp: One-time password to verify.
    """

    username: str = Field(
        ...,
        description='Email address or phone number',
        min_length=1,
        examples=['user@example.com', '+1234567890']
    )
    otp: str = Field(
        ...,
        description='One-time password',
        min_length=1,
        examples=['123456']
    )

    @field_validator('username', 'otp')
    @classmethod
    def validate_fields(cls, v: str) -> str:
        """Strip whitespace from fields."""
        return v.strip()


class UserInfo(BaseModel):
    """User information in response.

    Attributes:
        id: User ID.
        email: User email address.
        phone: User phone number.
        name: User name.
        role: User role (admin or user).
    """

    id: int = Field(..., description='User ID')
    email: Optional[str] = Field(None, description='User email address')
    phone: Optional[str] = Field(None, description='User phone number')
    name: Optional[str] = Field(None, description='User name')
    role: str = Field(..., description='User role', examples=['admin', 'user'])


class VerifyOTPResponse(BaseModel):
    """Response schema for OTP verification.

    Attributes:
        status: Response status (success or failed).
        status_code: HTTP status code.
        message: Response message.
        data: Response data containing access_token, refresh_token, and user.
    """

    status: str = Field(..., description='Response status', examples=['success'])
    status_code: int = Field(..., description='HTTP status code', examples=[200])
    message: str = Field(..., description='Response message')
    data: dict = Field(
        default_factory=dict,
        description='Response data containing access_token, refresh_token, and user'
    )

