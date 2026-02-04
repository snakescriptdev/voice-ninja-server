"""OTP-related API endpoints.

This module provides endpoints for OTP-based authentication:
- Request OTP: Send OTP to user's email or phone
- Verify OTP: Verify OTP and complete login
"""

from datetime import datetime, timedelta
from typing import Union

from fastapi import APIRouter, Request, HTTPException, status
from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
logger = setup_logger(__name__)
from app_v2.databases.models import UserModel, OAuthProviderModel, UnifiedAuthModel, UserNotificationSettings
from app_v2.utils.otp_utils import (
    generate_otp,
    is_email,
    is_phone,
    normalize_phone,
    send_otp_email,
    send_otp_sms,
)
from app_v2.utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
)

from app_v2.constants import (
    STATUS_SUCCESS,
    STATUS_FAILED,
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
    MSG_INVALID_EMAIL_OR_PHONE,
    MSG_USER_CREATED_OTP_SENT_EMAIL,
    MSG_USER_CREATED_OTP_SENT_SMS,
    MSG_OTP_SENT_EMAIL,
    MSG_OTP_SENT_SMS,
    MSG_FAILED_TO_SEND_OTP,
    MSG_USER_NOT_FOUND,
    MSG_USER_SIGNED_UP_WITH_GOOGLE,
    MSG_INVALID_OTP,
    MSG_OTP_EXPIRED,
    MSG_LOGIN_SUCCESSFUL,
    MSG_FAILED_TO_SEND_OTP_VIA_METHOD,
    MSG_OTP_RESENT_EMAIL,
    MSG_OTP_RESENT_SMS,
    MSG_NO_ACTIVE_OTP,
    OTP_EXPIRY_MINUTES,
    METHOD_EMAIL,
    METHOD_SMS,
)
from app_v2.schemas.otp import (
    RequestOTPRequest,
    RequestOTPResponse,
    ResendOTPRequest,
    VerifyOTPRequest,
    VerifyOTPResponse,
    ErrorResponse,
)

router = APIRouter(prefix='/api/v2/auth', tags=['Authentication'])


@router.post(
    '/login',
    status_code=status.HTTP_200_OK,
    summary='Request OTP',
    description='Send OTP to user email or phone number for authentication',
    responses={
        200: {
            'description': 'OTP sent successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 200,
                        'message': 'OTP sent to your email',
                        'data': {'method': 'email'}
                    }
                }
            }
        },
        400: {
            'description': 'Bad request - invalid input',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 400,
                        'message': 'Invalid email or phone format'
                    }
                }
            }
        },
        500: {
            'description': 'Internal server error',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 500,
                        'message': 'Failed to send OTP'
                    }
                }
            }
        }
    }
)
async def request_otp(request: RequestOTPRequest):
    """Request OTP to be sent to email or phone.

    This endpoint validates the username (email or phone), generates an OTP,
    and sends it via the appropriate channel.

    Args:
        request: Request containing username (email or phone).

    Returns:
        RequestOTPResponse with status and method information on success,
        ErrorResponse on failure.
    """
    try:
        username = request.username

        # Validate email or phone format
        is_email_login = is_email(username)
        is_phone_login = is_phone(username)

        if not is_email_login and not is_phone_login:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": MSG_INVALID_EMAIL_OR_PHONE
                }
            )

        # Normalize phone if needed
        if is_phone_login:
            username = normalize_phone(username)

        # Check unified auth model first
        unified_user = UnifiedAuthModel.get_by_username(username)
        user_created = False
        
        if not unified_user:
            # Create new user in unified auth
            unified_user = UnifiedAuthModel.create(
                email=username if is_email_login else '',
                phone=username if is_phone_login else '',
                has_otp_auth=True,
                is_verified=False
            )
            
            # Create default notification settings
            with db():
                notification_settings = UserNotificationSettings(user_id=unified_user.id)
                db.session.add(notification_settings)
                db.session.commit()

            user_created = True
            
            # Also create in old UserModel for backward compatibility
            with db():
                old_user = UserModel(
                    email=username if is_email_login else '',
                    phone=username if is_phone_login else '',
                    is_verified=False
                )
                db.session.add(old_user)
                db.session.commit()
                db.session.refresh(old_user)
        else:
            # User exists (may have signed up with Google or OTP previously)
            # Mark that they're using OTP auth if not already marked
            if not unified_user.has_otp_auth:
                UnifiedAuthModel.update(unified_user.id, has_otp_auth=True)
            
            # Ensure old user model exists
            old_user = UserModel.get_by_username(username)
            if not old_user:
                with db():
                    old_user = UserModel(
                        email=username if is_email_login else '',
                        phone=username if is_phone_login else '',
                        is_verified=unified_user.is_verified
                    )
                    db.session.add(old_user)
                    db.session.commit()
                    db.session.refresh(old_user)

        # Generate OTP
        otp = generate_otp()
        otp_expires = datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

        # Save OTP to unified user
        UnifiedAuthModel.update(
            unified_user.id,
            otp_code=otp,
            otp_expires_at=otp_expires
        )
        
        # Save OTP to old user too
        old_user = UserModel.get_by_username(username)
        if old_user:
            UserModel.update(
                old_user.id,
                otp_code=otp,
                otp_expires_at=otp_expires
            )

        # Send OTP - show correct message based on whether user was actually created
        if is_email_login:
            success = await send_otp_email(username, otp)
            method = METHOD_EMAIL
            success_message = MSG_USER_CREATED_OTP_SENT_EMAIL if user_created else MSG_OTP_SENT_EMAIL
        else:
            success = send_otp_sms(username, otp)
            method = METHOD_SMS
            success_message = MSG_USER_CREATED_OTP_SENT_SMS if user_created else MSG_OTP_SENT_SMS

        if not success:
            error_message = MSG_FAILED_TO_SEND_OTP_VIA_METHOD.format(method=method)
            logger.error(f'Failed to send OTP via {method} for user: {username}')
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                    "message": error_message
                }
            )

        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': success_message,
            'method': method
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in request_otp: {e}', exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": MSG_FAILED_TO_SEND_OTP
            }
        )


@router.post(
    '/verify-otp',
    status_code=status.HTTP_200_OK,
    summary='Verify OTP',
    description='Verify OTP and complete login process',
    responses={
        200: {
            'description': 'OTP verified successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 200,
                        'message': 'Login successful',
                        'data': {
                            'access_token': 'jwt_token',
                            'refresh_token': 'refresh_token',
                            'id': 1,
                            'email': 'user@example.com',
                            'role': 'user'
                        }
                    }
                }
            }
        },
        401: {
            'description': 'Unauthorized - invalid or expired OTP',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 401,
                        'message': 'Invalid OTP'
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
        },
        500: {
            'description': 'Internal server error',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 500,
                        'message': 'Login failed'
                    }
                }
            }
        }
    }
)
async def verify_otp(
    request: VerifyOTPRequest,
    http_request: Request
):
    """Verify OTP and complete login.

    This endpoint verifies the OTP, creates authentication tokens,
    and establishes a user session.

    Args:
        request: Request containing username and OTP.
        http_request: FastAPI request object for session management.

    Returns:
        VerifyOTPResponse with tokens and user information on success,
        ErrorResponse on failure.
    """
    try:
        username = request.username
        otp = request.otp

        # Normalize phone if needed
        if is_phone(username):
            username = normalize_phone(username)

        # Get user from unified model
        unified_user = UnifiedAuthModel.get_by_username(username)
        if not unified_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_401_UNAUTHORIZED,
                    "message": MSG_USER_NOT_FOUND
                }
            )

        # Verify OTP
        if not unified_user.otp_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_401_UNAUTHORIZED,
                    "message": "OTP not found. Please request OTP first."
                }
            )
        elif unified_user.otp_code != otp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_401_UNAUTHORIZED,
                    "message": "Wrong OTP. Please check and try again."
                }
            )

        # Check if OTP expired
        if not unified_user.otp_expires_at or datetime.now() > unified_user.otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_401_UNAUTHORIZED,
                    "message": MSG_OTP_EXPIRED
                }
            )
        is_new_user = not unified_user.is_verified

        # OTP is valid - clear it and verify user
        UnifiedAuthModel.update(
            unified_user.id,
            otp_code='',
            otp_expires_at=None,
            is_verified=True,
            last_login=datetime.now()
        )
        
        # Also update old model for backward compatibility
        old_user = UserModel.get_by_username(username)
        if old_user:
            UserModel.update(
                old_user.id,
                otp_code='',
                otp_expires_at=None,
                is_verified=True,
                last_login=datetime.now()
            )
            

        # Create tokens
        token_data = {
            'user_id': unified_user.id,
            'email': unified_user.email,
            'phone': unified_user.phone,
            'role': 'admin' if unified_user.is_admin else 'user'
        }
        access_token = create_access_token(data=token_data)
        refresh_token = create_refresh_token(unified_user.id)

        # Create session
        http_request.session['user'] = {
            'user_id': unified_user.id,
            'email': unified_user.email,
            'phone': unified_user.phone,
            'name': unified_user.name,
            'is_authenticated': True,
            'created_at': datetime.now().timestamp()
        }

        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': MSG_LOGIN_SUCCESSFUL,
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user': {
                'id': unified_user.id,
                'email': unified_user.email,
                'phone': unified_user.phone,
                'name': unified_user.name,
                'first_name': unified_user.first_name,
                'last_name': unified_user.last_name,
                'address': unified_user.address,
                'role': 'admin' if unified_user.is_admin else 'user',
                "is_new_user":is_new_user
                
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in verify_otp: {e}', exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": 'Login failed'
            }
        )


@router.post(
    '/resend-otp',
    status_code=status.HTTP_200_OK,
    summary='Resend OTP',
    description='Resend OTP to user email or phone number',
    responses={
        200: {
            'description': 'OTP resent successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 200,
                        'message': 'OTP resent to your email',
                        'data': {'method': 'email'}
                    }
                }
            }
        },
        400: {
            'description': 'Bad request - invalid input or no active OTP',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 400,
                        'message': 'Invalid email or phone format'
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
        },
        500: {
            'description': 'Internal server error',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 500,
                        'message': 'Failed to send OTP'
                    }
                }
            }
        }
    }
)
async def resend_otp(request: ResendOTPRequest):
    """Resend OTP to user email or phone.

    This endpoint validates the username, checks for an existing user with
    an active OTP, generates a new OTP, and resends it via the appropriate channel.

    Args:
        request: Request containing username (email or phone).

    Returns:
        RequestOTPResponse with status and method information on success,
        ErrorResponse on failure.
    """
    try:
        username = request.username

        # Validate email or phone format
        is_email_login = is_email(username)
        is_phone_login = is_phone(username)

        if not is_email_login and not is_phone_login:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": MSG_INVALID_EMAIL_OR_PHONE
                }
            )

        # Normalize phone if needed
        if is_phone_login:
            username = normalize_phone(username)

        # Get user from unified model
        unified_user = UnifiedAuthModel.get_by_username(username)
        if not unified_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_404_NOT_FOUND,
                    "message": MSG_USER_NOT_FOUND
                }
            )

        # Check if user has an active OTP (not expired)
        if not unified_user.otp_code or not unified_user.otp_expires_at or datetime.now() > unified_user.otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": MSG_NO_ACTIVE_OTP
                }
            )

        # Generate new OTP
        otp = generate_otp()
        otp_expires = datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

        # Update OTP in unified database
        UnifiedAuthModel.update(
            unified_user.id,
            otp_code=otp,
            otp_expires_at=otp_expires
        )
        
        # Also update old model for backward compatibility
        old_user = UserModel.get_by_username(username)
        if old_user:
            UserModel.update(
                old_user.id,
                otp_code=otp,
                otp_expires_at=otp_expires
            )

        # Send OTP
        if is_email_login:
            success = await send_otp_email(username, otp)
            method = METHOD_EMAIL
            success_message = MSG_OTP_RESENT_EMAIL
        else:
            success = send_otp_sms(username, otp)
            method = METHOD_SMS
            success_message = MSG_OTP_RESENT_SMS

        if not success:
            error_message = MSG_FAILED_TO_SEND_OTP_VIA_METHOD.format(method=method)
            logger.error(f'Failed to resend OTP via {method} for user: {username}')
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                    "message": error_message
                }
            )

        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': success_message,
            'method': method
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in resend_otp: {e}', exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": MSG_FAILED_TO_SEND_OTP
            }
        )
