"""Pydantic schemas for health check endpoints."""

from pydantic import BaseModel, Field


class HeartbeatResponse(BaseModel):
    """Response schema for heartbeat endpoint.

    Attributes:
        status_code: HTTP status code.
        status: Response status (success).
        message: Response message.
        data: Additional response data.
    """

    status_code: int = Field(..., description='HTTP status code', examples=[200])
    status: str = Field(..., description='Response status', examples=['success'])
    message: str = Field(..., description='Response message')
    data: dict = Field(
        default_factory=dict,
        description='Additional response data'
    )

