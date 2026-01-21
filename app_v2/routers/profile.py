"""Profile-related API endpoints.

This module provides endpoints for user profile management:
- Get user profile
- Update user profile
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi_sqlalchemy import db
from pydantic import ValidationError

from app_v2.core.logger import setup_logger
logger = setup_logger(__name__)

from app_v2.databases.models import UnifiedAuthModel
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.schemas.profile import (
    ProfileRequest,
    ProfileResponse,
    ProfileInfo,
)

from app_v2.constants import (
    STATUS_SUCCESS,
    STATUS_FAILED,
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
    MSG_PROFILE_UPDATED,
    MSG_PROFILE_RETRIEVED,
    MSG_USER_NOT_FOUND,
    MSG_INVALID_DATA,
)

# Security scheme for Bearer token
security = HTTPBearer()

router = APIRouter(prefix='/api/v2/profile', tags=['Profile'])


@router.get(
    '/',
    status_code=status.HTTP_200_OK,
    summary='Get user profile',
    description='Retrieve the current user\'s profile information',
    dependencies=[Depends(security)],
    responses={
        200: {
            'description': 'Profile retrieved successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 200,
                        'message': 'Profile retrieved successfully',
                        'data': {
                            'profile': {
                                'id': 1,
                                'email': 'user@example.com',
                                'phone': '1234567890',
                                'first_name': 'John',
                                'last_name': 'Doe',
                                'address': '123 Main St'
                            }
                        }
                    }
                }
            }
        },
        401: {
            'description': 'Unauthorized',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 401,
                        'message': 'Invalid or expired token'
                    }
                }
            }
        },
        404: {
            'description': 'User not found',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 404,
                        'message': 'User not found'
                    }
                }
            }
        }
    },
    openapi_extra={"security": [{"BearerAuth": []}]}
)
async def get_profile(current_user = Depends(get_current_user)):
    """Retrieve the current user's profile information.

    Args:
        current_user: The authenticated user.

    Returns:
        ProfileResponse with profile data on success.
    """
    try:
        return {
            "status": STATUS_SUCCESS,
            "status_code": HTTP_200_OK,
            "message": MSG_PROFILE_RETRIEVED,
            "profile": {
                "id": current_user.id,
                "email": current_user.email,
                "phone": current_user.phone,
                "first_name": current_user.first_name,
                "last_name": current_user.last_name,
                "address": current_user.address
            }
        }
    except Exception as e:
        logger.error(f"Error retrieving profile: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "Failed to retrieve profile"
            }
        )


@router.put(
    '/',
    status_code=status.HTTP_200_OK,
    summary='Update user profile',
    description='Update the current user\'s profile information',
    dependencies=[Depends(security)],
    responses={
        200: {
            'description': 'Profile updated successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 200,
                        'message': 'Profile updated successfully',
                        'data': {
                            'profile': {
                                'id': 1,
                                'email': 'user@example.com',
                                'phone': '1234567890',
                                'first_name': 'John',
                                'last_name': 'Doe',
                                'address': '123 Main St'
                            }
                        }
                    }
                }
            }
        },
        400: {
            'description': 'Bad request - invalid data',
            'content': {
                'application/json': {
                    'examples': {
                        'first_name_required': {
                            'summary': 'First name required',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'First name is required if other fields are provided'
                            }
                        },
                        'invalid_phone': {
                            'summary': 'Invalid phone number',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'phone: Phone number must be exactly 10 digits'
                            }
                        },
                        'phone_not_digits': {
                            'summary': 'Phone contains non-digits',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'phone: Phone number must contain only digits'
                            }
                        },
                        'invalid_name': {
                            'summary': 'Name contains numbers',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'first_name: Name must contain only letters, spaces, hyphens, and apostrophes'
                            }
                        },
                        'name_too_short': {
                            'summary': 'Name too short',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'first_name: Name must be at least 2 characters long'
                            }
                        },
                        'name_too_long': {
                            'summary': 'Name too long',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'first_name: Name must not exceed 50 characters'
                            }
                        }
                    }
                }
            }
        },
        401: {
            'description': 'Unauthorized',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 401,
                        'message': 'Invalid or expired token'
                    }
                }
            }
        },
        404: {
            'description': 'User not found',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 404,
                        'message': 'User not found'
                    }
                }
            }
        }
    },
    openapi_extra={"security": [{"BearerAuth": []}]}
)
async def update_profile(
    request: ProfileRequest,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    """Update the current user's profile information.

    Args:
        request: Profile update data.
        current_user: The authenticated user.

    Returns:
        ProfileResponse with updated profile data on success.
    """
    try:
        # Validation: If any field is provided, first_name must be provided
        if (request.last_name or request.phone or request.address) and not request.first_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": "First name is required if other fields are provided"
                }
            )

        # Update the user
        update_data = {}
        if request.first_name is not None:
            update_data['first_name'] = request.first_name
        if request.last_name is not None:
            update_data['last_name'] = request.last_name
        if request.phone is not None:
            update_data['phone'] = request.phone
        if request.address is not None:
            update_data['address'] = request.address

        updated_user = UnifiedAuthModel.update(current_user.id, **update_data)
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_404_NOT_FOUND,
                    "message": MSG_USER_NOT_FOUND
                }
            )

        return {
            "status": STATUS_SUCCESS,
            "status_code": HTTP_200_OK,
            "message": MSG_PROFILE_UPDATED,
            "profile": {
                "id": updated_user.id,
                "email": updated_user.email,
                "phone": updated_user.phone,
                "first_name": updated_user.first_name,
                "last_name": updated_user.last_name,
                "address": updated_user.address
            }
        }

    except HTTPException:
        raise
    except ValidationError as e:
        # Handle Pydantic validation errors
        error_messages = []
        for error in e.errors():
            field = error['loc'][-1]
            message = error['msg']
            error_messages.append(f"{field}: {message}")
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_400_BAD_REQUEST,
                "message": "; ".join(error_messages)
            }
        )
    except Exception as e:
        logger.error(f"Error updating profile: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "Failed to update profile"
            }
        )