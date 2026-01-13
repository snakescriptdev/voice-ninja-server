"""Response utility functions for consistent API responses."""

from typing import Optional, Dict, Any
from fastapi.responses import JSONResponse

from app_v2.constants import (
    STATUS,
    STATUS_CODE,
    MESSAGE,
    DATA,
    STATUS_SUCCESS,
    STATUS_FAILED,
)


def create_error_response(
    status_code: int,
    message: str,
    data: Optional[Dict[str, Any]] = None
) -> JSONResponse:
    """Create a standardized error response.

    Args:
        status_code: HTTP status code.
        message: Error message.
        data: Optional additional data.

    Returns:
        JSONResponse with standardized error format.
    """
    content: Dict[str, Any] = {
        STATUS_CODE: status_code,
        STATUS: STATUS_FAILED,
        MESSAGE: message,
    }
    if data:
        content[DATA] = data
    return JSONResponse(status_code=status_code, content=content)


def create_success_response(
    status_code: int,
    message: str,
    data: Optional[Dict[str, Any]] = None
) -> JSONResponse:
    """Create a standardized success response.

    Args:
        status_code: HTTP status code.
        message: Success message.
        data: Optional additional data.

    Returns:
        JSONResponse with standardized success format.
    """
    content: Dict[str, Any] = {
        STATUS_CODE: status_code,
        STATUS: STATUS_SUCCESS,
        MESSAGE: message,
    }
    if data:
        content[DATA] = data
    return JSONResponse(status_code=status_code, content=content)

