"""Pydantic schemas for user registration endpoint."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class RegisterRequest(BaseModel):
    """Request schema for user registration.

    Attributes:
        email: User email address.
        name: User full name.
    """

    email: str = Field(
        ...,
        description='User email address',
        min_length=1,
        examples=['user@example.com']
    )
    name: str = Field(
        ...,
        description='User full name',
        min_length=1,
        examples=['John Doe']
    )

    @field_validator('email', 'name')
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
