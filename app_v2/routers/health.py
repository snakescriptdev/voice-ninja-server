"""Health check and system status endpoints."""

from fastapi import APIRouter, status

from app.core import logger
from app_v2.constants import (
    STATUS_SUCCESS,
    HTTP_200_OK,
)
from app_v2.schemas.health import HeartbeatResponse

router = APIRouter(prefix='/api/v2', tags=['Health'])


@router.get(
    '/heartbeat',
    response_model=HeartbeatResponse,
    status_code=status.HTTP_200_OK,
    summary='Heartbeat',
    description='Check if the API server is running and ready to receive requests',
    responses={
        200: {
            'description': 'Server is running',
            'content': {
                'application/json': {
                    'example': {
                        'status_code': 200,
                        'status': 'success',
                        'message': 'Voice Ninja is running',
                        'data': {}
                    }
                }
            }
        }
    }
)
async def heartbeat() -> HeartbeatResponse:
    """Check if the API server is running.

    This endpoint provides a simple health check to verify that the API
    server is running and ready to receive requests.

    Returns:
        HeartbeatResponse with server status information.
    """
    logger.info('Heartbeat endpoint called')
    return HeartbeatResponse(
        status=STATUS_SUCCESS,
        status_code=HTTP_200_OK,
        message='Voice Ninja is running',
        data={}
    )

