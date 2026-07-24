from fastapi import APIRouter, HTTPException, status, Depends
from fastapi_sqlalchemy import db
from sqlalchemy import or_
from typing import Optional
from datetime import datetime, timezone
from math import ceil

from app_v2.databases.models import UnifiedAuthModel, SupportTicketModel
from app_v2.utils.jwt_utils import is_admin, HTTPBearer
from app_v2.schemas.enum_types import SupportTicketStatusEnum, SupportTicketCategoryEnum
from app_v2.schemas.support_schema import (
    SupportTicketAdminRead,
    SupportTicketAdminListResponse,
    SupportTicketAdminUpdate,
)
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(prefix="/api/v2/admin/support", tags=["Admin"], dependencies=[Depends(security), Depends(is_admin)])


# Statuses that mark a ticket as finished being worked on — transitioning
# into either of these (from any other status) stamps resolved_at, refreshing
# it every time the ticket re-enters resolved/closed (e.g. reopened then
# resolved again later) rather than only setting it once.
_TERMINAL_STATUSES = {SupportTicketStatusEnum.resolved, SupportTicketStatusEnum.closed}


def _to_admin_read(ticket: SupportTicketModel) -> SupportTicketAdminRead:
    user = ticket.user
    user_name = None
    if user:
        parts = [p for p in [user.first_name, user.last_name] if p]
        user_name = " ".join(parts) if parts else (user.name or None)

    return SupportTicketAdminRead(
        id=ticket.id,
        category=ticket.category,
        subject=ticket.subject,
        message=ticket.message,
        status=ticket.status,
        admin_response=ticket.admin_response,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        user_id=ticket.user_id,
        user_email=user.email if user else None,
        user_name=user_name,
    )


@router.get(
    "/tickets",
    response_model=SupportTicketAdminListResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_all_support_tickets(
    page: int = 1,
    size: int = 20,
    status_filter: Optional[SupportTicketStatusEnum] = None,
    category: Optional[SupportTicketCategoryEnum] = None,
    user_id: Optional[int] = None,
    search: Optional[str] = None,
):
    try:
        skip = (page - 1) * size

        query = db.session.query(SupportTicketModel).join(
            UnifiedAuthModel, SupportTicketModel.user_id == UnifiedAuthModel.id
        )

        if status_filter is not None:
            query = query.filter(SupportTicketModel.status == status_filter)
        if category is not None:
            query = query.filter(SupportTicketModel.category == category)
        if user_id is not None:
            query = query.filter(SupportTicketModel.user_id == user_id)
        if search:
            term = f"%{search}%"
            query = query.filter(
                or_(
                    SupportTicketModel.subject.ilike(term),
                    SupportTicketModel.message.ilike(term),
                )
            )

        total = query.count()
        tickets = (
            query.order_by(SupportTicketModel.created_at.desc())
            .offset(skip)
            .limit(size)
            .all()
        )

        total_pages = ceil(total / size) if size > 0 else 1

        return SupportTicketAdminListResponse(
            total=total,
            page=page,
            size=size,
            pages=total_pages,
            tickets=[_to_admin_read(t) for t in tickets],
        )
    except Exception as e:
        logger.error(f"Error listing all support tickets: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch support tickets")


@router.get(
    "/tickets/{ticket_id}",
    response_model=SupportTicketAdminRead,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def get_support_ticket_admin(ticket_id: int):
    try:
        ticket = db.session.query(SupportTicketModel).filter(
            SupportTicketModel.id == ticket_id
        ).first()
        if not ticket:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

        return _to_admin_read(ticket)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching support ticket {ticket_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch support ticket")


@router.patch(
    "/tickets/{ticket_id}",
    response_model=SupportTicketAdminRead,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def update_support_ticket(ticket_id: int, request: SupportTicketAdminUpdate):
    """
    Updates status and/or admin_response. When status transitions into
    'resolved' or 'closed', resolved_at is (re-)stamped with the current time
    on every such transition — not just the first time — so reopening a
    ticket and resolving it again reflects the latest resolution time.
    """
    try:
        ticket = db.session.query(SupportTicketModel).filter(
            SupportTicketModel.id == ticket_id
        ).first()
        if not ticket:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

        if request.status is not None:
            ticket.status = request.status
            if request.status in _TERMINAL_STATUSES:
                ticket.resolved_at = datetime.now(timezone.utc)

        if request.admin_response is not None:
            ticket.admin_response = request.admin_response

        db.session.add(ticket)
        db.session.commit()
        db.session.refresh(ticket)

        return _to_admin_read(ticket)
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating support ticket {ticket_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update support ticket")
