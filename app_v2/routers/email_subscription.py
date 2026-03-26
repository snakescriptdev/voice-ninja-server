"""
Email Subscription router.

Public endpoints (no auth):
  POST   /api/v2/subscribe          → subscribe email
  GET    /api/v2/unsubscribe/{token} → unsubscribe via token (safe for email links)

Admin endpoints (admin only):
  GET    /api/v2/admin/subscribers   → list all subscribers (paginated + filterable)
  DELETE /api/v2/admin/subscribers/{subscriber_id} → hard-delete a subscriber record
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks, Request
from fastapi_sqlalchemy import db
from sqlalchemy import func, desc

from app_v2.core.logger import setup_logger
from app_v2.databases.models import EmailSubscriberModel
from app_v2.schemas.email_subscription import (
    EmailSubscribeRequest,
    EmailSubscribeResponse,
    EmailUnsubscribeResponse,
    EmailSubscriberAdminItem,
)
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.utils.jwt_utils import is_admin, HTTPBearer
from app_v2.core.config import VoiceSettings
from app_v2.utils.email_service import send_welcome_subscription_email

logger = setup_logger(__name__)

# ── Public router (no auth) ────────────────────────────────────────────────────
public_router = APIRouter(prefix="/api/v2", tags=["Email Subscription"])

# ── Admin router ───────────────────────────────────────────────────────────────
admin_router = APIRouter(
    prefix="/api/v2/admin",
    tags=["Admin - Email Subscribers"],
    dependencies=[Depends(HTTPBearer()), Depends(is_admin)],
)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v2/subscribe
# ─────────────────────────────────────────────────────────────────────────────

@public_router.post(
    "/subscribe",
    response_model=EmailSubscribeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe to product updates",
)
def subscribe(body: EmailSubscribeRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Accepts an email address from the landing page and stores it as a subscriber.

    - If the email has already subscribed and is still active → returns 200 (idempotent).
    - If the email previously unsubscribed → re-activates the subscription.
    - New email → creates a fresh subscriber record.
    """
    try:
        email = body.email.lower().strip()

        existing = (
            db.session.query(EmailSubscriberModel)
            .filter(EmailSubscriberModel.email == email)
            .first()
        )

        if existing:
            if existing.is_active:
                # Already subscribed — idempotent
                return EmailSubscribeResponse(
                    message="You are already subscribed. We'll keep you posted!",
                    email=email,
                )
            # Re-activate
            existing.is_active = True
            existing.source = body.source or "landing_page"
            existing.subscribed_at = datetime.now(timezone.utc)
            existing.unsubscribed_at = None
            db.session.commit()
            logger.info("Re-subscribed: %s", email)
            
            # Send welcome email as background task
            background_tasks.add_task(
                send_welcome_subscription_email,
                email,
                existing.unsubscribe_token,
                request.base_url
            )
            
            return EmailSubscribeResponse(
                message="Welcome back! You've been re-subscribed.",
                email=email,
            )

        # New subscriber
        subscriber = EmailSubscriberModel(
            email=email,
            source=body.source or "landing_page",
        )
        db.session.add(subscriber)
        db.session.commit()
        logger.info("New subscriber: %s (source=%s)", email, subscriber.source)
        
        # Send welcome email as background task
        background_tasks.add_task(
            send_welcome_subscription_email,
            email,
            subscriber.unsubscribe_token,
            request.base_url
        )

        return EmailSubscribeResponse(
            message="Thanks for subscribing! You'll receive our latest product updates.",
            email=email,
        )

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error("Error subscribing %s: %s", body.email, e)
        raise HTTPException(status_code=500, detail="Failed to process subscription")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v2/unsubscribe/{token}
# ─────────────────────────────────────────────────────────────────────────────

@public_router.get(
    "/unsubscribe/{token}",
    response_model=EmailUnsubscribeResponse,
    summary="Unsubscribe via token (safe for one-click email links)",
)
def unsubscribe_by_token(token: str):
    """
    One-click unsubscribe endpoint. The token is included in product emails to
    allow safe, unathenticated opt-out without exposing the email address in the URL.

    Returns 200 even if the token is not found (to avoid leaking subscriber existence).
    """
    try:
        subscriber = (
            db.session.query(EmailSubscriberModel)
            .filter(EmailSubscriberModel.unsubscribe_token == token)
            .first()
        )

        if not subscriber or not subscriber.is_active:
            # Silent success — don't reveal whether the token exists
            return EmailUnsubscribeResponse(message="You have been unsubscribed.")

        subscriber.is_active = False
        subscriber.unsubscribed_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info("Unsubscribed via token: email=%s", subscriber.email)

        return EmailUnsubscribeResponse(message="You have been successfully unsubscribed.")

    except Exception as e:
        db.session.rollback()
        logger.error("Error unsubscribing token %s: %s", token, e)
        raise HTTPException(status_code=500, detail="Failed to process unsubscription")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v2/admin/subscribers
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.get(
    "/subscribers",
    response_model=PaginatedResponse[EmailSubscriberAdminItem],
    openapi_extra={"security": [{"BearerAuth": []}]},
    summary="List all email subscribers (admin)",
)
def list_subscribers(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    is_active: Optional[bool] = Query(None, description="Filter by active/unsubscribed status"),
    source: Optional[str] = Query(None, description="Filter by subscription source"),
    search: Optional[str] = Query(None, description="Search by email"),
):
    """
    Admin endpoint to list all email subscribers.
    Supports pagination, active/inactive filter, source filter, and email search.
    """
    try:
        query = db.session.query(EmailSubscriberModel)

        if is_active is not None:
            query = query.filter(EmailSubscriberModel.is_active == is_active)

        if source:
            query = query.filter(EmailSubscriberModel.source == source)

        if search:
            query = query.filter(EmailSubscriberModel.email.ilike(f"%{search}%"))

        query = query.order_by(desc(EmailSubscriberModel.subscribed_at))

        total = query.count()
        offset = (page - 1) * limit
        rows = query.offset(offset).limit(limit).all()

        pages = (total + limit - 1) // limit if limit > 0 else 0

        return PaginatedResponse(
            total=total,
            page=page,
            size=limit,
            pages=pages,
            items=[EmailSubscriberAdminItem.model_validate(r) for r in rows],
        )

    except Exception as e:
        logger.error("Error listing subscribers: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/v2/admin/subscribers/{subscriber_id}
# ─────────────────────────────────────────────────────────────────────────────

@admin_router.delete(
    "/subscribers/{subscriber_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra={"security": [{"BearerAuth": []}]},
    summary="Hard-delete a subscriber record (admin)",
)
def delete_subscriber(subscriber_id: int):
    """
    Permanently removes a subscriber record from the database.
    Prefer using the unsubscribe endpoint for normal opt-outs.
    """
    try:
        sub = db.session.query(EmailSubscriberModel).filter(
            EmailSubscriberModel.id == subscriber_id
        ).first()

        if not sub:
            raise HTTPException(status_code=404, detail="Subscriber not found")

        db.session.delete(sub)
        db.session.commit()
        logger.info("Admin hard-deleted subscriber id=%s (%s)", subscriber_id, sub.email)

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error("Error deleting subscriber %s: %s", subscriber_id, e)
        raise HTTPException(status_code=500, detail=str(e))
