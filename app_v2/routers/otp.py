"""OTP-related API endpoints.

This module provides endpoints for OTP-based authentication:
- Request OTP: Send OTP to user's email
- Verify OTP: Verify OTP and complete login
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Union

from fastapi import APIRouter, BackgroundTasks, Request, HTTPException, status, Depends
from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
logger = setup_logger(__name__)
from app_v2.databases.models import UserModel, OAuthProviderModel, UnifiedAuthModel, UserNotificationSettings, UserSessionModel, SupportTicketModel
from app_v2.utils.otp_utils import (
    generate_otp,
    is_email,
    send_otp_email,
)
from app_v2.utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
    create_user_session,
    revoke_session_by_jti,
    revoke_all_sessions,
    require_active_user,
    get_client_ip,
    parse_device_label,
)
from app_v2.utils.email_service import send_new_login_email
from app_v2.utils.coin_utils import grant_signup_credit, user_has_successful_payment

from app_v2.constants import (
    STATUS_SUCCESS,
    STATUS_FAILED,
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
    MSG_INVALID_EMAIL_FORMAT,
    MSG_USER_CREATED_OTP_SENT_EMAIL,
    MSG_OTP_SENT_EMAIL,
    MSG_FAILED_TO_SEND_OTP,
    MSG_USER_NOT_FOUND,
    MSG_USER_SIGNED_UP_WITH_GOOGLE,
    MSG_INVALID_OTP,
    MSG_OTP_EXPIRED,
    MSG_LOGIN_SUCCESSFUL,
    MSG_FAILED_TO_SEND_OTP_VIA_METHOD,
    MSG_OTP_RESENT_EMAIL,
    MSG_NO_ACTIVE_OTP,
    MSG_ACCOUNT_ALREADY_EXISTS,
    MSG_USER_NOT_FOUND_SIGNUP_PROMPT,
    HTTP_409_CONFLICT,
    OTP_EXPIRY_MINUTES,
    METHOD_EMAIL,
)
from app_v2.schemas.otp import (
    RequestOTPRequest,
    RequestOTPResponse,
    ResendOTPRequest,
    VerifyOTPRequest,
    VerifyOTPResponse,
    ErrorResponse,
    RefreshTokenRequest,
    LogoutRequest,
)

router = APIRouter(prefix='/api/v2/auth', tags=['Authentication'])


@router.post(
    '/login',
    status_code=status.HTTP_200_OK,
    summary='Request OTP',
    description='Send OTP to user email for authentication',
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
                        'message': 'Invalid email format'
                    }
                }
            }
        },
        404: {
            'description': "Login mode - no account exists for this email",
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 404,
                        'message': 'User not found. Please check your email or sign up.'
                    }
                }
            }
        },
        409: {
            'description': 'Signup mode - an account already exists for this email',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'failed',
                        'status_code': 409,
                        'message': 'Account already exists. Please login.'
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
    """Request OTP to be sent to email.

    This endpoint validates the username (email), generates an OTP,
    and sends it via email.

    The `mode` field ('login' or 'signup', default 'login') tells the two
    flows apart: 'signup' rejects with 409 if an account already exists for
    the email, and 'login' rejects with 404 if no account exists yet -
    instead of silently creating an account or resending a code either way.

    Args:
        request: Request containing username (email) and mode.

    Returns:
        RequestOTPResponse with status and method information on success,
        ErrorResponse on failure.
    """
    try:
        username = request.username
        mode = request.mode

        # Validate email format
        if not is_email(username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": MSG_INVALID_EMAIL_FORMAT
                }
            )

        # Check unified auth model first
        unified_user = UnifiedAuthModel.get_by_username(username)
        user_created = False

        # An account only really "exists" once it has completed at least one
        # signup (OTP verification or Google auth both set is_verified=True).
        # A row that exists but is_verified=False just means a previous
        # signup attempt was started but never finished, so it's fine to
        # treat it like a fresh signup and let the OTP be (re)sent.
        account_exists = bool(unified_user and unified_user.is_verified)

        if mode == 'signup' and account_exists:
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_409_CONFLICT,
                    "message": MSG_ACCOUNT_ALREADY_EXISTS
                }
            )

        if mode == 'login' and not account_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_404_NOT_FOUND,
                    "message": MSG_USER_NOT_FOUND_SIGNUP_PROMPT
                }
            )

        if not unified_user:
            # Create new user in unified auth
            unified_user = UnifiedAuthModel.create(
                username=username,
                email=username,
                phone="",
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
                    username=username,
                    email=username,
                    phone="",
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
                        username=unified_user.username or username,
                        email=unified_user.email,
                        phone=unified_user.phone,
                        is_verified=unified_user.is_verified
                    )
                    db.session.add(old_user)
                    db.session.commit()
                    db.session.refresh(old_user)

        # Generate OTP
        otp = generate_otp()
        otp_expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)

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
        success = await send_otp_email(username, otp)
        method = METHOD_EMAIL
        success_message = MSG_USER_CREATED_OTP_SENT_EMAIL if user_created else MSG_OTP_SENT_EMAIL

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
    http_request: Request,
    background_tasks: BackgroundTasks,
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
        if not unified_user.otp_expires_at or datetime.now(timezone.utc) > unified_user.otp_expires_at:
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
            last_login=datetime.now(timezone.utc)
        )

        # Grant the one-time free signup credit only on true first-time
        # signup (is_new_user was captured above, before is_verified was
        # flipped to True) — never on a plain re-login. grant_signup_credit
        # is itself idempotent via signup_credit_granted, this gate is just
        # to avoid the extra query on every ordinary login.
        if is_new_user:
            grant_signup_credit(unified_user.id)

        # Link any anonymous "contact us" tickets submitted with this same
        # email to this account, so they show up under the user's own
        # support tickets now that the email is confirmed to be theirs.
        if unified_user.email:
            linked = (
                db.session.query(SupportTicketModel)
                .filter(
                    SupportTicketModel.email == unified_user.email.lower(),
                    SupportTicketModel.user_id.is_(None),
                )
                .update({SupportTicketModel.user_id: unified_user.id}, synchronize_session=False)
            )
            if linked:
                db.session.commit()

        # Also update old model for backward compatibility
        old_user = UserModel.get_by_username(username)
        if old_user:
            UserModel.update(
                old_user.id,
                otp_code='',
                otp_expires_at=None,
                is_verified=True,
                last_login=datetime.now(timezone.utc)
            )
            

        # Create tokens. `jti` is minted once per login and embedded in both
        # the access and refresh tokens, and used as the key for the
        # server-side UserSessionModel row that makes revocation possible.
        jti = uuid.uuid4().hex
        token_data = {
            'user_id': unified_user.id,
            'email': unified_user.email,
            'phone': unified_user.phone,
            'role': 'admin' if unified_user.is_admin else 'user',
            'jti': jti,
        }
        access_token = create_access_token(data=token_data)
        refresh_token = create_refresh_token(unified_user.id, jti)

        # Record this login as a trackable/revocable server-side session.
        create_user_session(unified_user.id, jti, http_request)

        # Best-effort "new login" notification - sent after the response goes
        # out (via BackgroundTasks) so the SMTP round-trip never adds to
        # login latency; send_new_login_email already swallows its own
        # errors, so a failed send still can't break login.
        if unified_user.email:
            background_tasks.add_task(
                send_new_login_email,
                user_email=unified_user.email,
                user_name=unified_user.name,
                device_label=parse_device_label(http_request.headers.get("user-agent")),
                ip_address=get_client_ip(http_request),
                occurred_at=datetime.now(timezone.utc),
            )

        # Create session
        http_request.session['user'] = {
            'user_id': unified_user.id,
            'email': unified_user.email,
            'phone': unified_user.phone,
            'name': unified_user.name,
            'is_authenticated': True,
            'created_at': datetime.now(timezone.utc).timestamp()
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
                "is_new_user":is_new_user,
                # Included directly in the login response (rather than relying
                # on a follow-up /profile fetch) so a returning already-paid
                # user isn't shown the free-tier lock UI until a hard refresh.
                "has_paid": user_has_successful_payment(unified_user.id),
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
    description='Resend OTP to user email',
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
                        'message': 'Invalid email format'
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
    """Resend OTP to user email.

    This endpoint validates the username, checks for an existing user with
    an active OTP, generates a new OTP, and resends it via email.

    Args:
        request: Request containing username (email).

    Returns:
        RequestOTPResponse with status and method information on success,
        ErrorResponse on failure.
    """
    try:
        username = request.username

        # Validate email format
        if not is_email(username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": MSG_INVALID_EMAIL_FORMAT
                }
            )

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

        # Check if user has ever requested an OTP
        if not unified_user.otp_code or not unified_user.otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": MSG_NO_ACTIVE_OTP
                }
            )

        # Check if that OTP has since expired
        if datetime.now(timezone.utc) > unified_user.otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_400_BAD_REQUEST,
                    "message": MSG_OTP_EXPIRED
                }
            )

        # Generate new OTP
        otp = generate_otp()
        otp_expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)

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
        success = await send_otp_email(username, otp)
        method = METHOD_EMAIL
        success_message = MSG_OTP_RESENT_EMAIL

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


@router.post(
    '/refresh',
    status_code=status.HTTP_200_OK,
    summary='Refresh Token',
    description='Obtain a new access token using a valid refresh token',
    responses={
        200: {
            'description': 'Token refreshed successfully',
            'content': {
                'application/json': {
                    'example': {
                        'status': 'success',
                        'status_code': 200,
                        'message': 'Token refreshed successfully',
                        'data': {
                            'access_token': 'new_jwt_token',
                            'refresh_token': 'new_refresh_token'
                        }
                    }
                }
            }
        },
        401: {
            'description': 'Unauthorized - invalid or expired refresh token'
        }
    }
)
async def refresh_token(request: RefreshTokenRequest):
    """Refresh an access token.

    Validates the provided refresh token and issues a new access token
    and a new refresh token if valid.
    """
    try:
        user_id, jti = verify_refresh_token(request.refresh_token)
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_401_UNAUTHORIZED,
                    "message": "Invalid or expired refresh token"
                }
            )

        unified_user = UnifiedAuthModel.get_by_id(user_id)
        if not unified_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_401_UNAUTHORIZED,
                    "message": MSG_USER_NOT_FOUND
                }
            )

        if unified_user.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": 403,
                    "message": "User account suspended"
                }
            )

        # Refresh tokens predating session tracking carry no jti - reject
        # them the same way an unknown/revoked session would be, rather than
        # silently minting a session-less access token that would just fail
        # on its very first authenticated request anyway.
        session_row = UserSessionModel.get_by_jti(jti) if jti else None
        if not jti or not session_row or session_row.is_revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_401_UNAUTHORIZED,
                    "message": "Session has been revoked or does not exist"
                }
            )

        # Create new access token, reusing the SAME jti (no rotation) so it
        # keeps pointing at the same UserSessionModel row.
        token_data = {
            'user_id': unified_user.id,
            'email': unified_user.email,
            'phone': unified_user.phone,
            'role': 'admin' if unified_user.is_admin else 'user',
            'jti': jti,
        }
        access_token = create_access_token(data=token_data)

        # Bump last_used_at for this session on refresh too.
        try:
            with db():
                db.session.query(UserSessionModel).filter(
                    UserSessionModel.id == session_row.id
                ).update({"last_used_at": datetime.now(timezone.utc)})
                db.session.commit()
        except Exception as e:
            logger.error(f'Failed to bump last_used_at on refresh for jti={jti}: {e}', exc_info=True)

        return {
            'status_code': HTTP_200_OK,
            'message': 'Token refreshed successfully',
            'access_token': access_token,
            'refresh_token': request.refresh_token
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in refresh_token: {e}', exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": 'Failed to refresh token'
            }
        )


@router.post(
    '/logout',
    status_code=status.HTTP_200_OK,
    summary='Logout',
    description='Revoke the current session, or all sessions, ending real server-side auth (not just client-side token clearing)',
    responses={
        200: {
            'description': 'Logged out successfully',
            'content': {
                'application/json': {
                    'example': {
                        'message': 'Logged out'
                    }
                }
            }
        }
    }
)
async def logout(
    request: LogoutRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Log out the current user.

    - `revoke_all=True` revokes EVERY active session for this user,
      including the one used to call this endpoint (log out everywhere).
    - `revoke_all=False` (default) revokes only the session tied to the
      access token used to call this endpoint.
    """
    try:
        current_jti = getattr(current_user, "_current_jti", None)

        if request.revoke_all:
            revoke_all_sessions(current_user.id, exclude_jti=None)
        elif current_jti:
            revoke_session_by_jti(current_user.id, current_jti)

        return {
            'status': STATUS_SUCCESS,
            'status_code': HTTP_200_OK,
            'message': 'Logged out'
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in logout: {e}', exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": 'Failed to log out'
            }
        )
