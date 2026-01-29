"""OTP-related API endpoints.

This module provides endpoints for OTP-based authentication:
- Request OTP: Send OTP to user's email or phone
- Verify OTP: Verify OTP and complete login
"""

from datetime import datetime, timedelta
from typing import Union

from fastapi import APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.orm import Session

from app_v2.dependecies import get_db

from app_v2.core.logger import setup_logger
logger = setup_logger(__name__)

from app_v2.databases.models.users import UserModel, UnifiedAuthModel
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
    ResendOTPRequest,
    VerifyOTPRequest,
)

router = APIRouter(prefix='/api/v2/auth', tags=['Authentication'])


@router.post('/login', status_code=status.HTTP_200_OK)
async def request_otp(request: RequestOTPRequest, db: Session = Depends(get_db)):
    try:
        username = request.username

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

        if is_phone_login:
            username = normalize_phone(username)

        unified_user = UnifiedAuthModel.get_by_username(db, username)
        user_created = False

        if not unified_user:
            unified_user = UnifiedAuthModel.create(
                db,
                email=username if is_email_login else '',
                phone=username if is_phone_login else '',
                has_otp_auth=True,
                is_verified=False
            )
            user_created = True

            # Create old user record manually (since create() not available)
            old_user = UserModel(
                email=username if is_email_login else '',
                phone=username if is_phone_login else '',
                is_verified=False
            )
            db.add(old_user)
            db.commit()
            db.refresh(old_user)

        else:
            if not unified_user.has_otp_auth:
                UnifiedAuthModel.update(db, unified_user.id, has_otp_auth=True)

            old_user = UserModel.get_by_username(db, username)
            if not old_user:
                old_user = UserModel(
                    email=username if is_email_login else '',
                    phone=username if is_phone_login else '',
                    is_verified=unified_user.is_verified
                )
                db.add(old_user)
                db.commit()
                db.refresh(old_user)

        otp = generate_otp()
        otp_expires = datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

        UnifiedAuthModel.update(db, unified_user.id, otp_code=otp, otp_expires_at=otp_expires)

        old_user = UserModel.get_by_username(db, username)
        if old_user:
            UserModel.update(db, old_user.id, otp_code=otp, otp_expires_at=otp_expires)

        if is_email_login:
            success = await send_otp_email(username, otp)
            method = METHOD_EMAIL
            success_message = MSG_USER_CREATED_OTP_SENT_EMAIL if user_created else MSG_OTP_SENT_EMAIL
        else:
            success = send_otp_sms(username, otp)
            method = METHOD_SMS
            success_message = MSG_USER_CREATED_OTP_SENT_SMS if user_created else MSG_OTP_SENT_SMS

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "status": STATUS_FAILED,
                    "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                    "message": MSG_FAILED_TO_SEND_OTP_VIA_METHOD.format(method=method)
                }
            )

        return {
            "status": STATUS_SUCCESS,
            "status_code": HTTP_200_OK,
            "message": success_message,
            "method": method
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in request_otp: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": MSG_FAILED_TO_SEND_OTP
            }
        )


@router.post('/verify-otp', status_code=status.HTTP_200_OK)
async def verify_otp(request: VerifyOTPRequest, http_request: Request, db: Session = Depends(get_db)):
    try:
        username = request.username
        otp = request.otp

        if is_phone(username):
            username = normalize_phone(username)

        unified_user = UnifiedAuthModel.get_by_username(db, username)
        if not unified_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": STATUS_FAILED, "status_code": HTTP_401_UNAUTHORIZED, "message": MSG_USER_NOT_FOUND}
            )

        if not unified_user.otp_code or unified_user.otp_code != otp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": STATUS_FAILED, "status_code": HTTP_401_UNAUTHORIZED, "message": "Invalid OTP"}
            )

        if not unified_user.otp_expires_at or datetime.now() > unified_user.otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": STATUS_FAILED, "status_code": HTTP_401_UNAUTHORIZED, "message": MSG_OTP_EXPIRED}
            )

        UnifiedAuthModel.update(
            db,
            unified_user.id,
            otp_code='',
            otp_expires_at=None,
            is_verified=True,
            last_login=datetime.now()
        )

        old_user = UserModel.get_by_username(db, username)
        if old_user:
            UserModel.update(
                db,
                old_user.id,
                otp_code='',
                otp_expires_at=None,
                is_verified=True,
                last_login=datetime.now()
            )

        token_data = {
            'user_id': unified_user.id,
            'email': unified_user.email,
            'phone': unified_user.phone,
            'role': 'admin' if unified_user.is_admin else 'user'
        }

        access_token = create_access_token(data=token_data)
        refresh_token = create_refresh_token(unified_user.id)

        http_request.session['user'] = {
            'user_id': unified_user.id,
            'email': unified_user.email,
            'phone': unified_user.phone,
            'name': unified_user.name,
            'is_authenticated': True,
            'created_at': datetime.now().timestamp()
        }

        return {
            "status": STATUS_SUCCESS,
            "status_code": HTTP_200_OK,
            "message": MSG_LOGIN_SUCCESSFUL,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {
                "id": unified_user.id,
                "email": unified_user.email,
                "phone": unified_user.phone,
                "name": unified_user.name,
                "first_name": unified_user.first_name,
                "last_name": unified_user.last_name,
                "address": unified_user.address,
                "role": 'admin' if unified_user.is_admin else 'user',
                "is_new_user": False if old_user else True
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in verify_otp: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": STATUS_FAILED,
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "Login failed"
            }
        )


@router.post('/resend-otp', status_code=status.HTTP_200_OK)
async def resend_otp(request: ResendOTPRequest, db: Session = Depends(get_db)):
    try:
        username = request.username

        is_email_login = is_email(username)
        is_phone_login = is_phone(username)

        if not is_email_login and not is_phone_login:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"status": STATUS_FAILED, "status_code": HTTP_400_BAD_REQUEST, "message": MSG_INVALID_EMAIL_OR_PHONE}
            )

        if is_phone_login:
            username = normalize_phone(username)

        unified_user = UnifiedAuthModel.get_by_username(db, username)
        if not unified_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"status": STATUS_FAILED, "status_code": HTTP_404_NOT_FOUND, "message": MSG_USER_NOT_FOUND}
            )

        if not unified_user.otp_code or not unified_user.otp_expires_at or datetime.now() > unified_user.otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"status": STATUS_FAILED, "status_code": HTTP_400_BAD_REQUEST, "message": MSG_NO_ACTIVE_OTP}
            )

        otp = generate_otp()
        otp_expires = datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

        UnifiedAuthModel.update(db, unified_user.id, otp_code=otp, otp_expires_at=otp_expires)

        old_user = UserModel.get_by_username(db, username)
        if old_user:
            UserModel.update(db, old_user.id, otp_code=otp, otp_expires_at=otp_expires)

        if is_email_login:
            success = await send_otp_email(username, otp)
            method = METHOD_EMAIL
            success_message = MSG_OTP_RESENT_EMAIL
        else:
            success = send_otp_sms(username, otp)
            method = METHOD_SMS
            success_message = MSG_OTP_RESENT_SMS

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"status": STATUS_FAILED, "status_code": HTTP_500_INTERNAL_SERVER_ERROR, "message": MSG_FAILED_TO_SEND_OTP_VIA_METHOD.format(method=method)}
            )

        return {
            "status": STATUS_SUCCESS,
            "status_code": HTTP_200_OK,
            "message": success_message,
            "method": method
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in resend_otp: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": STATUS_FAILED, "status_code": HTTP_500_INTERNAL_SERVER_ERROR, "message": MSG_FAILED_TO_SEND_OTP}
        )
