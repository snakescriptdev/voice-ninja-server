"""User registration API endpoint.

This module provides endpoint for user registration:
- Register: Create new user account without password (OTP-based authentication)
"""

from fastapi import APIRouter, HTTPException, status
from fastapi_sqlalchemy import db

from app.core import logger
from app.databases.models import UserModel
from app.utils.helper import is_email, is_phone, normalize_phone

from app_v2.constants import (
    STATUS_SUCCESS,
    STATUS_FAILED,
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
    MSG_REGISTRATION_SUCCESSFUL,
    MSG_USER_ALREADY_EXISTS,
    MSG_USER_PHONE_ALREADY_EXISTS,
    MSG_INVALID_EMAIL_OR_PHONE,
    MSG_REGISTRATION_FAILED,
)
from app_v2.schemas.register import (
    RegisterRequest,
    RegisterResponse,
)

router = APIRouter(prefix='/api/v2', tags=['Authentication'])


@router.post(
    '/register',
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary='Register User',
    description='Register a new user with email or phone number (no password required). Users will login using OTP.',
    responses={
        201: {
            'description': 'User registered successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 201,
                        'message': 'User registered successfully',
                        'data': {
                            'id': 123,
                            'email': 'user@example.com',
                            'phone': None
                        }
                    }
                }
            }
        },
        400: {
            'description': 'Bad request - invalid input or user exists',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 400,
                        'message': 'User already exists',
                        'data': {}
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
                        'message': 'Registration failed',
                        'data': {}
                    }
                }
            }
        }
    }
)
async def register_user(request_data: RegisterRequest) -> RegisterResponse:
    """Register a new user.

    This endpoint creates a new user account without password.
    Users can login using OTP sent to their email or phone.

    Args:
        request_data: Request containing username (email/phone).

    Returns:
        RegisterResponse with user information.

    Raises:
        HTTPException: If validation fails or user already exists.
    """
    try:
        username = request_data.username.strip()

        # Validate username as email or phone
        is_email_input = is_email(username)
        is_phone_input = is_phone(username)

        if not is_email_input and not is_phone_input:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=MSG_INVALID_EMAIL_OR_PHONE
            )

        # Normalize phone if needed
        if is_phone_input:
            username = normalize_phone(username)

        # Check if user already exists
        existing_user = UserModel.get_by_username(username)
        if existing_user:
            error_message = MSG_USER_PHONE_ALREADY_EXISTS if is_phone_input else MSG_USER_ALREADY_EXISTS
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=error_message
            )

        # Create new user (no password needed)
        with db():
            user = UserModel(
                email=username if is_email_input else "",
                phone=username if is_phone_input else "",
                is_verified=False,
                tokens=20  # Default free tokens
            )
            db.session.add(user)
            db.session.commit()
            db.session.refresh(user)

        return RegisterResponse(
            status=STATUS_SUCCESS,
            status_code=HTTP_201_CREATED,
            message=MSG_REGISTRATION_SUCCESSFUL,
            data={
                'id': user.id,
                'email': user.email or None,
                'phone': user.phone or None
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in register_user: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=MSG_REGISTRATION_FAILED
        )
