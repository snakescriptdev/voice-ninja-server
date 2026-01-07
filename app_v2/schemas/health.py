"""Pydantic schemas for health check endpoints."""

from pydantic import BaseModel, Field


class HeartbeatResponse(BaseModel):
    """Response schema for heartbeat endpoint.

    Attributes:
        status: Response status (success).
        status_code: HTTP status code.
        message: Response message.
        data: Additional response data.
    """

    status: str = Field(..., description='Response status', examples=['success'])
    status_code: int = Field(..., description='HTTP status code', examples=[200])
    message: str = Field(..., description='Response message')
    data: dict = Field(
        default_factory=dict,
        description='Additional response data'
    )

