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

from app_v2.databases.models import UnifiedAuthModel, UserNotificationSettings
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.utils.feature_access import get_all_feature_limits
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
        with db():
            # Re-fetch user to ensure it's attached to the session
            # This fixes the DetachedInstanceError when accessing lazy-loaded notification_settings
            user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == current_user.id).first()
            
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "status": STATUS_FAILED,
                        "status_code": HTTP_404_NOT_FOUND,
                        "message": MSG_USER_NOT_FOUND
                    }
                )

            # Check if notification settings exist (legacy users), create if not
            if not user.notification_settings:
                notification_settings = UserNotificationSettings(user_id=user.id)
                db.session.add(notification_settings)
                db.session.commit()
                db.session.refresh(user)

            response = {
                "status": STATUS_SUCCESS,
                "status_code": HTTP_200_OK,
                "message": MSG_PROFILE_RETRIEVED,
                "profile": {
                    "id": user.id,
                    "email": user.email,
                    "phone": user.phone,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "address": user.address,
                    "role": "admin" if user.is_admin else "user",
                    "user_status": {
                        "current_status": "suspended" if user.is_suspended else "active",
                        "reason": user.suspension_reason
                    },
                    "is_new_user": (
                        (user.last_login - user.created_at).total_seconds() < 300 
                        if user.last_login and user.created_at else False
                    ),
                    "feature_limits": get_all_feature_limits(user.id)
                }
            }

            if user.notification_settings:
                response["profile"]["notification_settings"] = {
                    "id": user.notification_settings.id,
                    "email_notifications": user.notification_settings.email_notifications,
                    "useage_alerts": user.notification_settings.useage_alerts,
                    "expiry_alert": user.notification_settings.expiry_alert
                }
            
            return response
    except HTTPException:
        raise
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
                                'message': 'First name is required before other fields can be added'
                            }
                        },
                        'invalid_phone': {
                            'summary': 'Invalid phone number length',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'Phone must be between 10 and 15 digits long'
                            }
                        },
                        'phone_not_digits': {
                            'summary': 'Phone contains non-digits',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'Phone must contain only digits (spaces and hyphens are allowed as separators)'
                            }
                        },
                        'invalid_name': {
                            'summary': 'Name contains numbers or symbols',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'First name must contain only letters, spaces, hyphens, and apostrophes'
                            }
                        },
                        'name_too_short': {
                            'summary': 'Name too short',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'First name must be at least 2 characters long'
                            }
                        },
                        'name_too_long': {
                            'summary': 'Name too long',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'First name must not exceed 50 characters'
                            }
                        },
                        'invalid_address': {
                            'summary': 'Address contains disallowed characters',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': "Address can only contain letters, numbers, spaces, and , . ' - # / characters"
                            }
                        },
                        'first_last_name_same': {
                            'summary': 'First name and last name are the same',
                            'value': {
                                'status': 'failed',
                                'status_code': 400,
                                'message': 'First name and last name cannot be the same'
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
        # Validation: a first name must exist (either already on the account, or
        # provided in this request) before other profile fields can be set.
        has_first_name = bool(request.first_name) or bool(current_user.first_name)
        if (request.last_name or request.phone or request.address) and not has_first_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": "First name is required before other fields can be added"
                }
            )

        # Validation: first and last name can't be the same, whether both are
        # being changed in this request or only one is (falling back to the
        # value already stored on the account for the field not being changed).
        effective_first_name = request.first_name or current_user.first_name or ""
        effective_last_name = request.last_name or current_user.last_name or ""
        if (
            effective_first_name
            and effective_last_name
            and effective_first_name.strip().lower() == effective_last_name.strip().lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": "First name and last name cannot be the same"
                }
            )

        # Update the user
        update_data = request.model_dump(exclude_unset=True)
        
        # Prevent SQLAlchemy mapping error on relationship
        update_data.pop("notification_settings", None)

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

        with db():
            # Re-fetch user in this session to handle notification settings
            user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == current_user.id).first()
            
            # Update notification settings if provided
            if request.notification_settings:
                if not user.notification_settings:
                    # Create if defaults missing
                    settings = UserNotificationSettings(user_id=user.id)
                    db.session.add(settings)
                else:
                    settings = user.notification_settings
                
                if request.notification_settings.email_notifications is not None:
                    settings.email_notifications = request.notification_settings.email_notifications
                if request.notification_settings.useage_alerts is not None:
                    settings.useage_alerts = request.notification_settings.useage_alerts
                if request.notification_settings.expiry_alert is not None:
                    settings.expiry_alert = request.notification_settings.expiry_alert
                
                db.session.add(settings) # Ensure it's in session
                db.session.commit()
                db.session.refresh(user)

            response = {
                "status": STATUS_SUCCESS,
                "status_code": HTTP_200_OK,
                "message": MSG_PROFILE_UPDATED,
                "profile": {
                    "id": user.id,
                    "email": user.email,
                    "phone": user.phone,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "address": user.address,
                    "user_status": {
                        "current_status": "suspended" if user.is_suspended else "active",
                        "reason": user.suspension_reason
                    },
                    "feature_limits": get_all_feature_limits(user.id)
                }
            }
            
            # Add notification settings to response
            if user.notification_settings:
                response["profile"]["notification_settings"] = {
                    "id": user.notification_settings.id,
                    "email_notifications": user.notification_settings.email_notifications,
                    "useage_alerts": user.notification_settings.useage_alerts,
                    "expiry_alert": user.notification_settings.expiry_alert
                }
                
            return response

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