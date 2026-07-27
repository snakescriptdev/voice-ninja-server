"""Pydantic schemas for OTP-related endpoints."""

from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator

class RequestOTPRequest(BaseModel):
    """Request schema for requesting OTP.

    Attributes:
        username: Email address for OTP delivery.
        mode: Which flow the OTP is being requested for - 'login' or 'signup'.
            Defaults to 'login' to preserve existing behavior for older
            clients that don't send this field. Used to distinguish an
            existing-account error (signup) from a no-such-account error
            (login) instead of silently creating/logging in either way.
    """

    username: str = Field(
        ...,
        description='Email address',
        min_length=1,
        examples=['user@example.com']
    )
    mode: Literal['login', 'signup'] = Field(
        default='login',
        description="Flow requesting the OTP: 'login' or 'signup'",
        examples=['login']
    )

    @field_validator('username')
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Strip whitespace and lowercase the email."""
        return v.strip().lower()


class ResendOTPRequest(BaseModel):
    """Request schema for resending OTP.

    Attributes:
        username: Email address for OTP delivery.
    """

    username: str = Field(
        ...,
        description='Email address',
        min_length=1,
        examples=['user@example.com']
    )

    @field_validator('username')
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Strip whitespace and lowercase the email."""
        return v.strip().lower()


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
        username: Email address used to request OTP.
        otp: One-time password to verify.
    """

    username: str = Field(
        ...,
        description='Email address',
        min_length=1,
        examples=['user@example.com']
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

    @field_validator('username')
    @classmethod
    def validate_username_case(cls, v: str) -> str:
        """Convert email to lowercase."""
        return v.lower()


class UserInfo(BaseModel):
    """User information in response.

    Attributes:
        id: User ID.
        email: User email address.
        phone: User phone number.
        name: User name.
        first_name: User first name.
        last_name: User last name.
        address: User address.
        role: User role (admin or user).
    """

    id: int = Field(..., description='User ID')
    email: Optional[str] = Field(None, description='User email address')
    phone: Optional[str] = Field(None, description='User phone number')
    name: Optional[str] = Field(None, description='User name')
    first_name: Optional[str] = Field(None, description='User first name')
    last_name: Optional[str] = Field(None, description='User last name')
    address: Optional[str] = Field(None, description='User address')
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


class ErrorResponse(BaseModel):
    """Standard error response schema.

    Attributes:
        status: Response status (always 'failed' for errors).
        status_code: HTTP status code.
        message: Error message.
        data: Optional additional error data.
    """

    status: str = Field(..., description='Response status', examples=['failed'])
    status_code: int = Field(..., description='HTTP status code', examples=[400])
    message: str = Field(..., description='Error message')
    data: Optional[dict] = Field(
        default=None,
        description='Optional additional error data'
    )


class RefreshTokenRequest(BaseModel):
    """Request schema for refreshing an access token.

    Attributes:
        refresh_token: The refresh token issued during login.
    """
    refresh_token: str = Field(
        ...,
        description='Refresh token for issuing a new access token',
        min_length=1
    )


class LogoutRequest(BaseModel):
    """Request schema for logging out.

    Attributes:
        revoke_all: If True, revoke every active session for this user
            ("log out everywhere"). If False (default), revoke only the
            session tied to the access token used to call this endpoint.
    """
    revoke_all: bool = Field(
        default=False,
        description='Revoke all sessions (log out everywhere) instead of just the current one'
    )

