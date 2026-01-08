"""OTP-related API endpoints.

This module provides endpoints for OTP-based authentication:
- Request OTP: Send OTP to user's email or phone
- Verify OTP: Verify OTP and complete login
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Request, HTTPException, status
from fastapi_sqlalchemy import db

from app.core import logger
from app.databases.models import UserModel
from app.utils.helper import (
    generate_otp,
    is_email,
    is_phone,
    normalize_phone,
    send_otp_email,
    send_otp_sms,
)
from app.utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
)

from app_v2.constants import (
    STATUS_SUCCESS,
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_500_INTERNAL_SERVER_ERROR,
    MSG_INVALID_EMAIL_OR_PHONE,
    MSG_USER_CREATED_OTP_SENT_EMAIL,
    MSG_USER_CREATED_OTP_SENT_SMS,
    MSG_OTP_SENT_EMAIL,
    MSG_OTP_SENT_SMS,
    MSG_FAILED_TO_SEND_OTP,
    MSG_USER_NOT_FOUND,
    MSG_INVALID_OTP,
    MSG_OTP_EXPIRED,
    MSG_LOGIN_SUCCESSFUL,
    MSG_FAILED_TO_SEND_OTP_VIA_METHOD,
    OTP_EXPIRY_MINUTES,
    METHOD_EMAIL,
    METHOD_SMS,
)
from app_v2.schemas.otp import (
    RequestOTPRequest,
    RequestOTPResponse,
    VerifyOTPRequest,
    VerifyOTPResponse,
)

router = APIRouter(prefix='/api/v2', tags=['Authentication'])


@router.post(
    '/login',
    response_model=RequestOTPResponse,
    status_code=status.HTTP_200_OK,
    summary='Request OTP',
    description='Send OTP to user email or phone number for authentication',
    responses={
        400: {
            'description': 'Bad request - invalid input',
            'content': {
                'application/json': {
                    'example': {
                        'status_code': 400,
                        'status': 'failed',
                        'message': 'Username required'
                    }
                }
            }
        },
        500: {
            'description': 'Internal server error',
            'content': {
                'application/json': {
                    'example': {
                        'status_code': 500,
                        'status': 'failed',
                        'message': 'Failed to send OTP'
                    }
                }
            }
        }
    }
)
async def request_otp(request: RequestOTPRequest) -> RequestOTPResponse:
    """Request OTP to be sent to email or phone.

    This endpoint validates the username (email or phone), generates an OTP,
    and sends it via the appropriate channel.

    Args:
        request: Request containing username (email or phone).

    Returns:
        RequestOTPResponse with status and method information.

    Raises:
        HTTPException: If validation fails or OTP cannot be sent.
    """
    try:
        username = request.username

        # Validate email or phone format
        is_email_login = is_email(username)
        is_phone_login = is_phone(username)

        if not is_email_login and not is_phone_login:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=MSG_INVALID_EMAIL_OR_PHONE
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in request_otp: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MSG_FAILED_TO_SEND_OTP
        )


@router.post(
    '/verify-otp',
    response_model=VerifyOTPResponse,
    status_code=status.HTTP_200_OK,
    summary='Verify OTP',
    description='Verify OTP and complete login process',
    responses={
        400: {
            'description': 'Bad request - missing required fields',
            'content': {
                'application/json': {
                    'example': {
                        'status_code': 400,
                        'status': 'failed',
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
                        'status_code': 401,
                        'status': 'failed',
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
                        'status_code': 500,
                        'status': 'failed',
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
) -> VerifyOTPResponse:
    """Verify OTP and complete login.

    This endpoint verifies the OTP, creates authentication tokens,
    and establishes a user session.

    Args:
        request: Request containing username and OTP.
        http_request: FastAPI request object for session management.

    Returns:
        VerifyOTPResponse with tokens and user information.

    Raises:
        HTTPException: If validation fails or OTP is invalid/expired.
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
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=MSG_USER_NOT_FOUND
            )

        # Verify OTP
        if not user.otp_code or user.otp_code != otp:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=MSG_INVALID_OTP
            )

        # Check if OTP expired
        if not user.otp_expires_at or datetime.now() > user.otp_expires_at:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=MSG_OTP_EXPIRED
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in verify_otp: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Login failed'
        )

