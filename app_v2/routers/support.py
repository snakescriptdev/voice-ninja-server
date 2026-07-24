from fastapi import APIRouter, HTTPException, status, Depends
from fastapi_sqlalchemy import db
from typing import Optional
from math import ceil

from app_v2.databases.models import UnifiedAuthModel, SupportTicketModel
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer
from app_v2.schemas.enum_types import SupportTicketStatusEnum
from app_v2.schemas.support_schema import (
    SupportTicketCreate,
    SupportTicketRead,
    SupportTicketListResponse,
)
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(prefix="/api/v2/support", tags=["Support"])


@router.post(
    "/tickets",
    response_model=SupportTicketRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def create_support_ticket(
    request: SupportTicketCreate,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    try:
        ticket = SupportTicketModel(
            user_id=current_user.id,
            category=request.category,
            subject=request.subject,
            message=request.message,
            status=SupportTicketStatusEnum.open,
        )
        db.session.add(ticket)
        db.session.commit()
        db.session.refresh(ticket)
        return SupportTicketRead.model_validate(ticket)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating support ticket: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create support ticket")


@router.get(
    "/tickets",
    response_model=SupportTicketListResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def list_support_tickets(
    page: int = 1,
    size: int = 20,
    status_filter: Optional[SupportTicketStatusEnum] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    try:
        skip = (page - 1) * size

        query = db.session.query(SupportTicketModel).filter(
            SupportTicketModel.user_id == current_user.id
        )

        if status_filter is not None:
            query = query.filter(SupportTicketModel.status == status_filter)

        total = query.count()
        tickets = (
            query.order_by(SupportTicketModel.created_at.desc())
            .offset(skip)
            .limit(size)
            .all()
        )

        total_pages = ceil(total / size) if size > 0 else 1

        return SupportTicketListResponse(
            total=total,
            page=page,
            size=size,
            pages=total_pages,
            tickets=[SupportTicketRead.model_validate(t) for t in tickets],
        )
    except Exception as e:
        logger.error(f"Error listing support tickets: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch support tickets")


@router.get(
    "/tickets/{ticket_id}",
    response_model=SupportTicketRead,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def get_support_ticket(
    ticket_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    try:
        ticket = (
            db.session.query(SupportTicketModel)
            .filter(
                SupportTicketModel.id == ticket_id,
                SupportTicketModel.user_id == current_user.id,
            )
            .first()
        )
        # Same 404 whether the ticket doesn't exist or belongs to another
        # user — never leak the existence of another user's ticket.
        if not ticket:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

        return SupportTicketRead.model_validate(ticket)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching support ticket {ticket_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch support ticket")
