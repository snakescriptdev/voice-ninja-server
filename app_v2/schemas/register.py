"""Pydantic schemas for user registration endpoint."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class RegisterRequest(BaseModel):
    """Request schema for user registration.

    Attributes:
        username: User email or phone number.
    """

    username: str = Field(
        ...,
        description='User email or phone number',
        min_length=1,
        examples=['user@example.com', '+1234567890']
    )

    @field_validator('username')
    @classmethod
    def validate_fields(cls, v: str) -> str:
        """Strip whitespace from fields."""
        return v.strip()


class RegisterResponse(BaseModel):
    """Response schema for user registration.

    Attributes:
        status: Response status (success or failed).
        status_code: HTTP status code.
        message: Response message.
        data: Response data containing user information.
    """

    status: str = Field(..., description='Response status', examples=['success'])
    status_code: int = Field(..., description='HTTP status code', examples=[201])
    message: str = Field(..., description='Response message')
    data: dict = Field(
        default_factory=dict,
        description='Response data containing user information'
    )
