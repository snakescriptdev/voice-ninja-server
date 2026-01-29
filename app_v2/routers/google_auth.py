"""Google OAuth authentication endpoints.

This module provides endpoints for Google OAuth authentication:
- Login with Google: Get authorization URL
- Google Callback: Handle OAuth callback and authenticate user
"""

import os
import secrets
from datetime import datetime, timedelta
from typing import Union
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse,JSONResponse
from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
logger = setup_logger(__name__)
from app_v2.databases.models import UserModel, OAuthProviderModel, UnifiedAuthModel
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
from app_v2.schemas.otp import ErrorResponse

router = APIRouter(prefix='/api/v2/auth', tags=['Authentication'])

# Google OAuth configuration
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')
FRONTEND_URL = os.getenv('FRONTEND_URL')

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/auth'
GOOGLE_TOKEN_URL = 'https://accounts.google.com/o/oauth2/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v1/userinfo'

# In-memory store for one-time auth codes (use Redis in production)
auth_code_store = {}


@router.get(
    '/google/login',
    summary='Login with Google',
    description='Get Google OAuth authorization URL to redirect user for authentication.',
)
async def google_login(request: Request):
    """Generate Google OAuth authorization URL.
    
    Args:
        request: FastAPI request object to get the host URL.
    
    Returns:
        GoogleLoginResponse with authorization URL.
    """
    try:
        # Use the redirect_uri from environment or build dynamically from request
        redirect_uri = GOOGLE_REDIRECT_URI
        
        # Log for debugging
        logger.info(f'Using redirect URI: {redirect_uri}')
        
        # Build OAuth URL with proper URL encoding
        params = {
            'response_type': 'code',
            'client_id': GOOGLE_CLIENT_ID,
            'redirect_uri': redirect_uri,
            'scope': 'openid profile email',
            'access_type': 'offline',
             'prompt': 'consent'
        }
        
        auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
        
        logger.info(f'Generated auth URL: {auth_url}')
        
        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': 'Google authorization URL generated',
            'url': auth_url,
            'redirect_uri': redirect_uri  # Return this for debugging
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
async def google_callback(code: str, http_request: Request):
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
        GoogleCallbackResponse with tokens and user info on success,
        ErrorResponse on failure.
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
        
        logger.info(f'Exchanging code for token with redirect_uri: {GOOGLE_REDIRECT_URI}')
        
        token_response = requests.post(
                        GOOGLE_TOKEN_URL,
                        data=token_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=10
)
        
        if token_response.status_code != 200:
            error_detail = token_response.json() if token_response.text else {}
            logger.error(f'Google token error (status {token_response.status_code}): {token_response.text}')
            
            # Return detailed error message
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
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        
        if userinfo_response.status_code != 200:
            logger.error(f'Google userinfo error: {userinfo_response.text}')
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
        unified_user = UnifiedAuthModel.get_by_email(google_email)
        
        if unified_user:
            # User exists - add Google auth if not already added
            if not unified_user.has_google_auth:
                UnifiedAuthModel.update(
                    unified_user.id,
                    has_google_auth=True,
                    google_user_id=google_user_id,
                    is_verified=True
                )
            
            # Update last login
            UnifiedAuthModel.update(unified_user.id, last_login=datetime.now())
            user_created = False
            user_id = unified_user.id
            user_email = unified_user.email
            user_phone = unified_user.phone
            user_is_admin = unified_user.is_admin
        else:
            # Check by Google ID in case email changed
            unified_user = UnifiedAuthModel.get_by_google_id(google_user_id)
            
            if unified_user:
                # User exists with this Google ID
                UnifiedAuthModel.update(unified_user.id, last_login=datetime.now())
                user_created = False
                user_id = unified_user.id
                user_email = unified_user.email
                user_phone = unified_user.phone
                user_is_admin = unified_user.is_admin
            else:
                # Create new user in unified auth
                unified_user = UnifiedAuthModel.create(
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
        
        # Also maintain old models for backward compatibility
        oauth_record = OAuthProviderModel.get_by_provider_and_user_id('google', google_user_id)
        
        if not oauth_record:
            # Check if user exists in old UserModel
            old_user = UserModel.get_by_email(google_email)
            
            if not old_user:
                # Create in old model too
                with db():
                    old_user = UserModel(
                        email=google_email,
                        name=google_name,
                        is_verified=True,
                        tokens=20
                    )
                    db.session.add(old_user)
                    db.session.commit()
                    db.session.refresh(old_user)
            
            # Create OAuth provider record
            OAuthProviderModel.create(
                user_id=old_user.id,
                provider='google',
                provider_user_id=google_user_id,
                email=google_email
            )
            
            # Update old user last login
            UserModel.update(old_user.id, last_login=datetime.now())
        else:
            # Update existing user last login
            UserModel.update(oauth_record.user_id, last_login=datetime.now())
        
        # Create JWT tokens
        token_data = {
            'user_id': user_id,
            'email': user_email,
            'phone': user_phone,
            'role': 'admin' if user_is_admin else 'user'
        }
        access_token_jwt = create_access_token(data=token_data)
        refresh_token_jwt = create_refresh_token(user_id)
        
        # Generate one-time authorization code
        app_code = secrets.token_urlsafe(32)
        
        # Store the code with tokens and user info (expires in 5 minutes)
        auth_code_store[app_code] = {
            'access_token': access_token_jwt,
            'refresh_token': refresh_token_jwt,
            'user': {
                'id': user_id,
                'email': user_email,
                'phone': user_phone,
                'role': 'admin' if user_is_admin else 'user',
                'is_new_user': user_created
            },
            'expires_at': datetime.now() + timedelta(minutes=5),
            'used': False
        }
        
        # Redirect to frontend with the one-time code
        frontend_callback_url = f"{FRONTEND_URL}/auth/callback?code={app_code}"
        logger.info(f'Redirecting to frontend: {frontend_callback_url}')
        
        # Check if the request is from a browser fetch/XHR (like Swagger UI)
        # by looking at Accept header or a custom header
        accept_header = http_request.headers.get("accept", "")
        if "application/json" in accept_header and "text/html" not in accept_header:
            logger.info("AJAX request detected, returning JSON instead of redirect")
            return {
                'status': STATUS_SUCCESS,
                'status_code': HTTP_200_OK,
                'message': 'Authentication successful',
                'code': app_code,
                'frontend_url': frontend_callback_url
            }
        
        return RedirectResponse(url=frontend_callback_url)
    
    except HTTPException as http_exc:
        # Check for AJAX request in error case too
        accept_header = http_request.headers.get("accept", "")
        error_message = http_exc.detail.get('message', 'Authentication failed') if isinstance(http_exc.detail, dict) else str(http_exc.detail)
        
        if "application/json" in accept_header and "text/html" not in accept_header:
            return JSONResponse(
                status_code=http_exc.status_code,
                content={
                    "status": STATUS_FAILED,
                    "status_code": http_exc.status_code,
                    "message": error_message
                }
            )

        # Redirect to frontend with error
        frontend_error_url = f"{FRONTEND_URL}/auth/callback?error={error_message}"
        logger.error(f'OAuth error, redirecting to: {frontend_error_url}')
        return RedirectResponse(url=frontend_error_url)
    except Exception as e:
        logger.error(f'Error in Google callback: {e}', exc_info=True)
        # Check for AJAX request in unexpected error case
        accept_header = http_request.headers.get("accept", "")
        if "application/json" in accept_header and "text/html" not in accept_header:
             return JSONResponse(
                status_code=HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                    "message": "Google authentication failed"
                }
            )
        # Redirect to frontend with error
        frontend_error_url = f"{FRONTEND_URL}/auth/callback?error=Google authentication failed"
        return RedirectResponse(url=frontend_error_url)


@router.post(
    '/exchange',
    status_code=status.HTTP_200_OK,
    summary='Exchange authorization code for tokens',
    description='Exchange one-time authorization code for access and refresh tokens',
    responses={
        200: {
            'description': 'Tokens exchanged successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 200,
                        'message': 'Tokens exchanged successfully',
                        'access_token': 'eyJhbGci...',
                        'refresh_token': 'eyJhbGci...',
                        'is_new_user': False,
                        'user': {
                            'id': 1,
                            'email': 'user@gmail.com',
                            'phone': '',
                            'role': 'user',
                            
                        }
                    }
                }
            }
        },
        400: {
            'description': 'Invalid or expired authorization code',
            'content': {
                'application/json': {
                    'example': {
                        'detail': {
                            'message': 'Invalid or expired authorization code',
                            'status': 'failed',
                            'status_code': 400
                        }
                    }
                }
            }
        }
    }
)
async def exchange_auth_code(request: Request):
    """Exchange one-time authorization code for access and refresh tokens.
    
    This endpoint:
    1. Receives the one-time authorization code from frontend
    2. Validates the code
    3. Returns JWT tokens and user info
    4. Invalidates the code after use
    
    Args:
        request: FastAPI request object containing the code
        
    Returns:
        Dictionary with tokens and user info on success
    """
    try:
        body = await request.json()
        code = body.get('code')
        
        if not code:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": 'Authorization code is required'
                }
            )
        
        # Check if code exists
        if code not in auth_code_store:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": 'Invalid or expired authorization code'
                }
            )
        
        code_data = auth_code_store[code]
        
        # Check if code has been used
        if code_data['used']:
            del auth_code_store[code]  # Clean up
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": 'Authorization code has already been used'
                }
            )
        
        # Check if code has expired
        if datetime.now() > code_data['expires_at']:
            del auth_code_store[code]  # Clean up
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": 'Authorization code has expired'
                }
            )
        
        # Mark code as used and get tokens
        code_data['used'] = True
        access_token = code_data['access_token']
        refresh_token = code_data['refresh_token']
        user = code_data['user'].copy()
        is_new_user = user.pop('is_new_user', False)
        # Clean up the code from store
        del auth_code_store[code]
        
        logger.info(f'Successfully exchanged code for user: {user["email"]}')
        
        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': 'Tokens exchanged successfully',
            'access_token': access_token,
            'refresh_token': refresh_token,
            'is_new_user': is_new_user,
            'user': user
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in exchange endpoint: {e}', exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": 'Failed to exchange authorization code'
            }
        )
