"""Google OAuth authentication endpoints.

This module provides endpoints for Google OAuth authentication:
- Login with Google: Get authorization URL
- Google Callback: Handle OAuth callback and authenticate user
"""

import os
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy.orm import Session

from app_v2.dependecies import get_db
from app_v2.core.logger import setup_logger
from app_v2.databases.models.users import UserModel, OAuthProviderModel, UnifiedAuthModel
from app_v2.utils.jwt_utils import create_access_token, create_refresh_token

from app_v2.constants import (
    STATUS_SUCCESS,
    STATUS_FAILED,
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

logger = setup_logger(__name__)

router = APIRouter(prefix='/api/v2/auth', tags=['Authentication'])

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/auth'
GOOGLE_TOKEN_URL = 'https://accounts.google.com/o/oauth2/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v1/userinfo'


@router.get(
    '/google/login',
    summary='Login with Google',
    description='Get Google OAuth authorization URL to redirect user for authentication.',
)
async def google_login():
    """Generate Google OAuth authorization URL."""
    try:
        auth_url = (
            f"{GOOGLE_AUTH_URL}?"
            f"response_type=code&"
            f"client_id={GOOGLE_CLIENT_ID}&"
            f"redirect_uri={GOOGLE_REDIRECT_URI}&"
            f"scope=openid%20profile%20email&"
            f"access_type=offline"
        )

        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': 'Google authorization URL generated',
            'url': auth_url
        }

    except Exception as e:
        logger.error(f'Error generating Google auth URL: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": 'Failed to generate authorization URL'
            }
        )


@router.get(
    '/google/callback',
    summary='Google OAuth Callback',
    description='Handle Google OAuth callback and authenticate user.',
)
async def google_callback(
    code: str,
    http_request: Request,
    db: Session = Depends(get_db)
):
    """Handle Google OAuth callback."""
    try:
        logger.info(f'Received authorization code from Google (length: {len(code)})')

        # Exchange code for access token
        token_data = {
            'code': code,
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'redirect_uri': GOOGLE_REDIRECT_URI,
            'grant_type': 'authorization_code',
        }

        token_response = requests.post(GOOGLE_TOKEN_URL, data=token_data)

        if token_response.status_code != 200:
            error_detail = token_response.json() if token_response.text else {}
            error_message = error_detail.get('error_description', 'Failed to get access token from Google')

            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": f'Google OAuth error: {error_message}'
                }
            )

        access_token = token_response.json().get('access_token')
        if not access_token:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": 'No access token received from Google'
                }
            )

        # Get user info from Google
        userinfo_response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}'}
        )

        if userinfo_response.status_code != 200:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": 'Failed to get user info from Google'
                }
            )

        google_user = userinfo_response.json()
        google_email = google_user.get('email')
        google_user_id = google_user.get('id')
        google_name = google_user.get('name', '')

        if not google_email or not google_user_id:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": 'Invalid user info from Google'
                }
            )

        # Check unified auth model first
        unified_user = UnifiedAuthModel.get_by_email(db, google_email)

        if unified_user:
            if not unified_user.has_google_auth:
                UnifiedAuthModel.update(
                    db,
                    unified_user.id,
                    has_google_auth=True,
                    google_user_id=google_user_id,
                    is_verified=True
                )

            UnifiedAuthModel.update(db, unified_user.id, last_login=datetime.now())
            user_created = False

        else:
            unified_user = UnifiedAuthModel.get_by_google_id(db, google_user_id)

            if unified_user:
                UnifiedAuthModel.update(db, unified_user.id, last_login=datetime.now())
                user_created = False
            else:
                unified_user = UnifiedAuthModel.create(
                    db,
                    email=google_email,
                    name=google_name,
                    has_google_auth=True,
                    google_user_id=google_user_id,
                    is_verified=True,
                    tokens=20,
                    last_login=datetime.now()
                )
                user_created = True

        user_id = unified_user.id
        user_email = unified_user.email
        user_phone = unified_user.phone
        user_is_admin = unified_user.is_admin

        # Maintain old models for backward compatibility
        oauth_record = OAuthProviderModel.get_by_provider_and_user_id(
            db, 'google', google_user_id
        )

        if not oauth_record:
            old_user = UserModel.get_by_email(db, google_email)

            if not old_user:
                old_user = UserModel(
                    email=google_email,
                    name=google_name,
                    is_verified=True,
                    tokens=20
                )

            OAuthProviderModel.create(
                db,
                user_id=old_user.id,
                provider='google',
                provider_user_id=google_user_id,
                email=google_email
            )

            UserModel.update(db, old_user.id, last_login=datetime.now())
        else:
            UserModel.update(db, oauth_record.user_id, last_login=datetime.now())

        # Create JWT tokens
        token_data = {
            'user_id': user_id,
            'email': user_email,
            'phone': user_phone,
            'role': 'admin' if user_is_admin else 'user'
        }

        access_token_jwt = create_access_token(data=token_data)
        refresh_token_jwt = create_refresh_token(user_id)

        # Create session
        http_request.session['user'] = {
            'user_id': user_id,
            'email': user_email,
            'phone': user_phone,
            'is_authenticated': True,
            'created_at': datetime.now().timestamp()
        }

        message = 'User created successfully and logged in with Google' if user_created else 'Login successful'

        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': message,
            'access_token': access_token_jwt,
            'refresh_token': refresh_token_jwt,
            'id': user_id,
            'email': user_email,
            'phone': user_phone,
            'role': 'admin' if user_is_admin else 'user',
            'is_new_user': user_created
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in Google callback: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": 'Google authentication failed'
            }
        )
