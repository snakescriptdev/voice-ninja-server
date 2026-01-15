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
from app_v2.databases.models import UserModel, OAuthProviderModel
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
    response_model=Union[RequestOTPResponse, ErrorResponse],
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
async def request_otp(request: RequestOTPRequest) -> Union[RequestOTPResponse, ErrorResponse]:
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
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_400_BAD_REQUEST,
                message=MSG_INVALID_EMAIL_OR_PHONE
            )

        # Normalize phone if needed
        if is_phone_login:
            username = normalize_phone(username)

        # Get or create user
        user = UserModel.get_by_username(username)
        user_created = False
        if not user:
            # Create new user
            user_created = True
            with db():
                user = UserModel(
                    email=username if is_email_login else '',
                    phone=username if is_phone_login else '',
                    is_verified=False
                )
                db.session.add(user)
                db.session.commit()
                db.session.refresh(user)
        else:
            # Check if user signed up with Google (only for email login)
            if is_email_login:
                oauth_record = OAuthProviderModel.get_by_provider_and_email('google', username)
                if oauth_record:
                    return ErrorResponse(
                        status=STATUS_FAILED,
                        status_code=HTTP_400_BAD_REQUEST,
                        message=MSG_USER_SIGNED_UP_WITH_GOOGLE
                    )

        # Generate OTP
        otp = generate_otp()
        otp_expires = datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

        # Save OTP to user
        UserModel.update(
            user.id,
            otp_code=otp,
            otp_expires_at=otp_expires
        )

        # Send OTP
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
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_500_INTERNAL_SERVER_ERROR,
                message=error_message
            )

        return RequestOTPResponse(
            status=STATUS_SUCCESS,
            status_code=HTTP_200_OK,
            message=success_message,
            data={'method': method}
        )

    except Exception as e:
        logger.error(f'Error in request_otp: {e}', exc_info=True)
        return ErrorResponse(
            status=STATUS_FAILED,
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            message=MSG_FAILED_TO_SEND_OTP
        )


@router.post(
    '/verify-otp',
    response_model=Union[VerifyOTPResponse, ErrorResponse],
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
        400: {
            'description': 'Bad request - missing required fields',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 400,
                        'message': 'Username and OTP required'
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
) -> Union[VerifyOTPResponse, ErrorResponse]:
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

        # Get user
        user = UserModel.get_by_username(username)
        if not user:
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_401_UNAUTHORIZED,
                message=MSG_USER_NOT_FOUND
            )

        # Verify OTP
        if not user.otp_code or user.otp_code != otp:
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_401_UNAUTHORIZED,
                message=MSG_INVALID_OTP
            )

        # Check if OTP expired
        if not user.otp_expires_at or datetime.now() > user.otp_expires_at:
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_401_UNAUTHORIZED,
                message=MSG_OTP_EXPIRED
            )

        # OTP is valid - clear it and verify user
        UserModel.update(
            user.id,
            otp_code='',
            otp_expires_at=None,
            is_verified=True,
            last_login=datetime.now()
        )

        # Create tokens
        token_data = {
            'user_id': user.id,
            'email': user.email,
            'phone': user.phone,
            'role': 'admin' if user.is_admin else 'user'
        }
        access_token = create_access_token(data=token_data)
        refresh_token = create_refresh_token(user.id)

        # Create session
        http_request.session['user'] = {
            'user_id': user.id,
            'email': user.email,
            'phone': user.phone,
            'name': user.name,
            'is_authenticated': True,
            'created_at': datetime.now().timestamp()
        }

        return VerifyOTPResponse(
            status=STATUS_SUCCESS,
            status_code=HTTP_200_OK,
            message=MSG_LOGIN_SUCCESSFUL,
            data={
                'access_token': access_token,
                'refresh_token': refresh_token,
                'id': user.id,
                'email': user.email,
                'phone': user.phone,
                'role': 'admin' if user.is_admin else 'user'
            }
        )

    except Exception as e:
        logger.error(f'Error in verify_otp: {e}', exc_info=True)
        return ErrorResponse(
            status=STATUS_FAILED,
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            message='Login failed'
        )


@router.post(
    '/resend-otp',
    response_model=Union[RequestOTPResponse, ErrorResponse],
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
async def resend_otp(request: ResendOTPRequest) -> Union[RequestOTPResponse, ErrorResponse]:
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
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_400_BAD_REQUEST,
                message=MSG_INVALID_EMAIL_OR_PHONE
            )

        # Normalize phone if needed
        if is_phone_login:
            username = normalize_phone(username)

        # Get user
        user = UserModel.get_by_username(username)
        if not user:
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_404_NOT_FOUND,
                message=MSG_USER_NOT_FOUND
            )

        # Check if user has an active OTP (not expired)
        if not user.otp_code or not user.otp_expires_at or datetime.now() > user.otp_expires_at:
            return ErrorResponse(
                status=STATUS_FAILED,
                status_code=HTTP_400_BAD_REQUEST,
                message=MSG_NO_ACTIVE_OTP
            )

        # Check if user signed up with Google (only for email login)
        if is_email_login:
            oauth_record = OAuthProviderModel.get_by_provider_and_email('google', username)
            if oauth_record:
                return ErrorResponse(
                    status=STATUS_FAILED,
                    status_code=HTTP_400_BAD_REQUEST,
                    message=MSG_USER_SIGNED_UP_WITH_GOOGLE
                )

        # Generate new OTP
        otp = generate_otp()
        otp_expires = datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

        # Update OTP in database
        UserModel.update(
            user.id,
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
                status_code=HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_message
            )

        return RequestOTPResponse(
            status=STATUS_SUCCESS,
            status_code=HTTP_200_OK,
            message=success_message,
            data={'method': method}
        )

    except Exception as e:
        logger.error(f'Error in resend_otp: {e}', exc_info=True)
        return ErrorResponse(
            status=STATUS_FAILED,
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            message=MSG_FAILED_TO_SEND_OTP
        )

