"""Google OAuth authentication endpoints.

This module provides endpoints for Google OAuth authentication:
- Login with Google: Get authorization URL
- Google Callback: Handle OAuth callback and authenticate user
"""

import os
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
logger = setup_logger(__name__)
from app_v2.databases.models import UserModel, OAuthProviderModel
from app_v2.utils.jwt_utils import create_access_token, create_refresh_token

from app_v2.constants import (
    STATUS_SUCCESS,
    STATUS_FAILED,
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
)
from app_v2.schemas.google_auth import (
    GoogleLoginResponse,
    GoogleCallbackResponse,
)

router = APIRouter(prefix='/api/v2', tags=['Authentication'])

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/auth'
GOOGLE_TOKEN_URL = 'https://accounts.google.com/o/oauth2/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v1/userinfo'


@router.get(
    '/auth/google/login',
    response_model=GoogleLoginResponse,
    summary='Login with Google',
    description='Get Google OAuth authorization URL to redirect user for authentication.',
)
async def google_login() -> GoogleLoginResponse:
    """Generate Google OAuth authorization URL.
    
    Returns:
        GoogleLoginResponse with authorization URL.
    """
    try:
        auth_url = (
            f"{GOOGLE_AUTH_URL}?"
            f"response_type=code&"
            f"client_id={GOOGLE_CLIENT_ID}&"
            f"redirect_uri={GOOGLE_REDIRECT_URI}&"
            f"scope=openid%20profile%20email&"
            f"access_type=offline"
        )
        
        return GoogleLoginResponse(
            status=STATUS_SUCCESS,
            status_code=HTTP_200_OK,
            message='Google authorization URL generated',
            data={'url': auth_url}
        )
    
    except Exception as e:
        logger.error(f'Error generating Google auth URL: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to generate authorization URL'
        )


@router.get(
    '/auth/google/callback',
    response_model=GoogleCallbackResponse,
    summary='Google OAuth Callback',
    description='Handle Google OAuth callback and authenticate user.',
)
async def google_callback(code: str, http_request: Request) -> GoogleCallbackResponse:
    """Handle Google OAuth callback.
    
    This endpoint:
    1. Exchanges authorization code for access token
    2. Fetches user info from Google
    3. Checks if user exists or can be created
    4. Returns JWT tokens for authentication
    
    Args:
        code: Authorization code from Google
        http_request: FastAPI request object
        
    Returns:
        GoogleCallbackResponse with tokens and user info.
        
    Raises:
        HTTPException: If authentication fails or user already exists with different method.
    """
    try:
        # Log the code received for debugging
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
            logger.error(f'Google token error (status {token_response.status_code}): {token_response.text}')
            
            # Return detailed error message
            error_message = error_detail.get('error_description', 'Failed to get access token from Google')
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f'Google OAuth error: {error_message}'
            )
        
        access_token = token_response.json().get('access_token')
        
        if not access_token:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail='No access token received from Google'
            )
        
        # Get user info from Google
        userinfo_response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}'}
        )
        
        if userinfo_response.status_code != 200:
            logger.error(f'Google userinfo error: {userinfo_response.text}')
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail='Failed to get user info from Google'
            )
        
        google_user = userinfo_response.json()
        google_email = google_user.get('email')
        google_user_id = google_user.get('id')
        google_name = google_user.get('name', '')
        
        if not google_email or not google_user_id:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail='Invalid user info from Google'
            )
        
        # Check if OAuth provider record exists
        oauth_record = OAuthProviderModel.get_by_provider_and_user_id('google', google_user_id)
        
        if oauth_record:
            # User already signed up with Google - just login
            user = UserModel.get_by_id(oauth_record.user_id)
            user_created = False
        else:
            # Check if user exists with this email (signed up via OTP)
            existing_user = UserModel.get_by_email(google_email)
            
            if existing_user:
                # User exists but signed up with OTP, can't use Google
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail='User already exists with this email. Please login using OTP instead.'
                )
            
            # Create new user with Google
            with db():
                user = UserModel(
                    email=google_email,
                    name=google_name,
                    is_verified=True,  # Google accounts are pre-verified
                    tokens=20  # Default free tokens
                )
                db.session.add(user)
                db.session.commit()
                db.session.refresh(user)
                
                # Create OAuth provider record
                OAuthProviderModel.create(
                    user_id=user.id,
                    provider='google',
                    provider_user_id=google_user_id,
                    email=google_email
                )
            
            user_created = True
        
        # Update last login
        UserModel.update(user.id, last_login=datetime.now())
        
        # Create JWT tokens
        token_data = {
            'user_id': user.id,
            'email': user.email,
            'phone': user.phone,
            'role': 'admin' if user.is_admin else 'user'
        }
        access_token_jwt = create_access_token(data=token_data)
        refresh_token_jwt = create_refresh_token(user.id)
        
        # Create session
        http_request.session['user'] = {
            'user_id': user.id,
            'email': user.email,
            'phone': user.phone,
            'is_authenticated': True,
            'created_at': datetime.now().timestamp()
        }
        
        message = 'User created successfully and logged in with Google' if user_created else 'Login successful'
        
        return GoogleCallbackResponse(
            status=STATUS_SUCCESS,
            status_code=HTTP_200_OK,
            message=message,
            data={
                'access_token': access_token_jwt,
                'refresh_token': refresh_token_jwt,
                'id': user.id,
                'email': user.email,
                'phone': user.phone,
                'role': 'admin' if user.is_admin else 'user',
                'is_new_user': user_created
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in Google callback: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Google authentication failed'
        )
