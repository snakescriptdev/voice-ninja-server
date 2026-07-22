from fastapi import APIRouter, HTTPException, status, Depends, Query, Response
from fastapi_sqlalchemy import db
from sqlalchemy import func, or_, desc, select
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from app_v2.databases.models import UnifiedAuthModel, AgentModel, PhoneNumberService, CoinsLedgerModel, ActivityLogModel, APICallLogModel, VoiceModel, ConversationsModel, PaymentModel, PaymentTypeEnum
from app_v2.utils.jwt_utils import is_admin, HTTPBearer
from app_v2.schemas.admin_user_management import UserManagementStats, UserManagementListItem, SuspendUserRequest,AdjustUserCoinRequest, AdminUserTransactionItem, AdminUserBillingHistoryItem
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.utils.time_utils import format_time_ago
from app_v2.core.logger import setup_logger

from app_v2.utils.coin_utils import admin_adjust_coins, get_user_coin_balance
from app_v2.utils.agent_summary import build_agent_summaries
from app_v2.schemas.user_dashboard import AgentSummaryItem
from app_v2.utils.invoice_utils import generate_invoice_pdf

security = HTTPBearer()
logger = setup_logger(__name__)
router = APIRouter(prefix="/api/v2/admin/user-management", tags=["Admin"],dependencies=[Depends(security),Depends(is_admin)])

@router.get("/stats", response_model=UserManagementStats,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_management_stats():
    """
    Get general user management statistics.
    """
    try:
        # Total users (non-admin)
        total_users = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.is_admin.is_(False)).count()

        now = datetime.now(timezone.utc)
        first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        new_signups_this_month = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.is_admin.is_(False),
            UnifiedAuthModel.created_at >= first_day_of_month,
        ).count()

        suspended_users = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.is_admin.is_(False),
            UnifiedAuthModel.is_suspended.is_(True),
        ).count()

        total_coins_consumed = db.session.query(
            func.sum(func.abs(CoinsLedgerModel.coins))
        ).filter(CoinsLedgerModel.coins < 0).scalar() or 0

        return {
            "total_users": total_users,
            "new_signups_this_month": new_signups_this_month,
            "suspended_users": suspended_users,
            "total_coins_consumed": int(total_coins_consumed),
        }
    except Exception as e:
        logger.error(f"Error in get_user_management_stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users", response_model=PaginatedResponse[UserManagementListItem],openapi_extra={"security":[{"BearerAuth":[]}]})
def list_users_managed(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1),
    search: Optional[str] = None,
    is_suspended: Optional[bool] = Query(None),
    sort_order: str = Query("desc", enum=["asc", "desc"]),
    sort_by: str = Query("last_active", enum=["last_active", "credits_consumed", "date_added"]),
):
    """
    Paginated, searchable, and filtered user listing for admin.
    Default sorted by last active; also supports credits consumed and date added.
    """
    try:
        # Subqueries for counts
        agent_subquery = db.session.query(
            AgentModel.user_id,
            func.count(AgentModel.id).label("agent_count")
        ).group_by(AgentModel.user_id).subquery()

        phone_subquery = db.session.query(
            PhoneNumberService.user_id,
            func.count(PhoneNumberService.id).label("phone_count")
        ).group_by(PhoneNumberService.user_id).subquery()

        coins_subquery = (
            db.session.query(
                CoinsLedgerModel.user_id,
                CoinsLedgerModel.balance_after.label("balance")
            )
            .distinct(CoinsLedgerModel.user_id)
            .order_by(
                CoinsLedgerModel.user_id,
                CoinsLedgerModel.created_at.desc(),
                CoinsLedgerModel.id.desc()
            )
            .subquery()
        )

        voice_subquery = db.session.query(
            VoiceModel.user_id,
            func.count(VoiceModel.id).label("voice_count")
        ).filter(VoiceModel.is_custom_voice.is_(True)).group_by(VoiceModel.user_id).subquery()

        last_active_subquery = db.session.query(
            ActivityLogModel.user_id,
            func.max(ActivityLogModel.created_at).label("last_active")
        ).group_by(ActivityLogModel.user_id).subquery()

        credits_consumed_subquery = (
            db.session.query(
                CoinsLedgerModel.user_id,
                func.sum(func.abs(CoinsLedgerModel.coins)).label("credits_consumed"),
            )
            .filter(CoinsLedgerModel.coins < 0)
            .group_by(CoinsLedgerModel.user_id)
            .subquery()
        )

        now = datetime.now(timezone.utc)
        month_ago = now - timedelta(days=30)
        week_ago  = now - timedelta(days=7)

        calls_total_subquery = db.session.query(
            APICallLogModel.user_id,
            func.count(APICallLogModel.id).label("calls_total")
        ).group_by(APICallLogModel.user_id).subquery()

        calls_monthly_subquery = db.session.query(
            APICallLogModel.user_id,
            func.count(APICallLogModel.id).label("calls_monthly")
        ).filter(APICallLogModel.created_at >= month_ago)\
         .group_by(APICallLogModel.user_id).subquery()

        calls_weekly_subquery = db.session.query(
            APICallLogModel.user_id,
            func.count(APICallLogModel.id).label("calls_weekly")
        ).filter(APICallLogModel.created_at >= week_ago)\
         .group_by(APICallLogModel.user_id).subquery()

        # Main query
        query = db.session.query(
            UnifiedAuthModel.id.label("user_id"),
            UnifiedAuthModel.name.label("username"),
            UnifiedAuthModel.first_name,
            UnifiedAuthModel.email,
            UnifiedAuthModel.is_suspended,
            func.coalesce(coins_subquery.c.balance, 0).label("balance_coins"),
            func.coalesce(agent_subquery.c.agent_count, 0).label("no_of_agents"),
            func.coalesce(phone_subquery.c.phone_count, 0).label("no_of_phones"),
            func.greatest(
                UnifiedAuthModel.last_login,
                last_active_subquery.c.last_active
            ).label("last_active"),
            func.coalesce(calls_total_subquery.c.calls_total, 0).label("calls_total"),
            func.coalesce(calls_monthly_subquery.c.calls_monthly, 0).label("calls_monthly"),
            func.coalesce(calls_weekly_subquery.c.calls_weekly, 0).label("calls_weekly"),
            func.coalesce(voice_subquery.c.voice_count, 0).label("no_of_voices"),
            func.coalesce(credits_consumed_subquery.c.credits_consumed, 0).label("credits_consumed"),
            UnifiedAuthModel.created_at.label("date_added"),
        ).filter(UnifiedAuthModel.is_admin.is_(False))\
         .outerjoin(agent_subquery, UnifiedAuthModel.id == agent_subquery.c.user_id)\
         .outerjoin(phone_subquery, UnifiedAuthModel.id == phone_subquery.c.user_id)\
         .outerjoin(coins_subquery, UnifiedAuthModel.id == coins_subquery.c.user_id)\
         .outerjoin(last_active_subquery, UnifiedAuthModel.id == last_active_subquery.c.user_id)\
         .outerjoin(calls_total_subquery, UnifiedAuthModel.id == calls_total_subquery.c.user_id)\
         .outerjoin(calls_monthly_subquery, UnifiedAuthModel.id == calls_monthly_subquery.c.user_id)\
         .outerjoin(calls_weekly_subquery, UnifiedAuthModel.id == calls_weekly_subquery.c.user_id)\
         .outerjoin(voice_subquery, UnifiedAuthModel.id == voice_subquery.c.user_id)\
         .outerjoin(credits_consumed_subquery, UnifiedAuthModel.id == credits_consumed_subquery.c.user_id)

        # Search
        if search:
            query = query.filter(
                or_(
                    UnifiedAuthModel.first_name.ilike(f"%{search}%"),
                    UnifiedAuthModel.name.ilike(f"%{search}%"),
                    UnifiedAuthModel.email.ilike(f"%{search}%")
                )
            )

        # Suspended Filter
        if is_suspended is not None:
            query = query.filter(UnifiedAuthModel.is_suspended == is_suspended)

        # Sorting — default last active, or credits consumed / date added on request.
        sort_column_map = {
            "last_active": last_active_subquery.c.last_active,
            "credits_consumed": func.coalesce(credits_consumed_subquery.c.credits_consumed, 0),
            "date_added": UnifiedAuthModel.created_at,
        }
        order_attr = sort_column_map.get(sort_by, last_active_subquery.c.last_active)
        if sort_order == "desc":
            query = query.order_by(desc(order_attr))
        else:
            query = query.order_by(order_attr)

        # Pagination
        total_count = query.count()
        offset = (page - 1) * limit
        results = query.offset(offset).limit(limit).all()

        items = [
            UserManagementListItem(
                user_id=r.user_id,
                username=r.first_name or r.username or "Unknown",
                email=r.email or "",
                balance_coins=int(r.balance_coins),
                no_of_agents=r.no_of_agents,
                no_of_phones=r.no_of_phones,
                last_active=format_time_ago(r.last_active) if r.last_active else "long time ago",
                is_suspended=r.is_suspended,
                api_calls_total=r.calls_total,
                api_calls_monthly=r.calls_monthly,
                api_calls_weekly=r.calls_weekly,
                no_of_voices=r.no_of_voices,
                credits_consumed=int(r.credits_consumed),
                date_added=r.date_added.isoformat() if r.date_added else None,
            ) for r in results
        ]

        total_pages = (total_count + limit - 1) // limit if limit > 0 else 0

        return PaginatedResponse(
            total=total_count,
            page=page,
            size=limit,
            pages=total_pages,
            items=items
        )

    except Exception as e:
        logger.error(f"Error listing users managed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users/{user_id}", response_model=UserManagementListItem, openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_detail(user_id: int):
    """Single-user detail for the admin user-detail page (same shape as the list item)."""
    try:
        user = db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.id == user_id,
            UnifiedAuthModel.is_admin.is_(False),
        ).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        now = datetime.now(timezone.utc)
        month_ago = now - timedelta(days=30)
        week_ago = now - timedelta(days=7)

        def _count(model):
            return db.session.query(func.count(model.id)).filter(model.user_id == user_id).scalar() or 0

        no_of_agents = _count(AgentModel)
        no_of_phones = _count(PhoneNumberService)
        no_of_voices = db.session.query(func.count(VoiceModel.id)).filter(
            VoiceModel.user_id == user_id, VoiceModel.is_custom_voice.is_(True)
        ).scalar() or 0
        calls_total = _count(APICallLogModel)
        calls_monthly = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.user_id == user_id, APICallLogModel.created_at >= month_ago
        ).scalar() or 0
        calls_weekly = db.session.query(func.count(APICallLogModel.id)).filter(
            APICallLogModel.user_id == user_id, APICallLogModel.created_at >= week_ago
        ).scalar() or 0
        last_activity = db.session.query(func.max(ActivityLogModel.created_at)).filter(
            ActivityLogModel.user_id == user_id
        ).scalar()
        last_active = max([d for d in (user.last_login, last_activity) if d], default=None)

        credits_consumed = db.session.query(
            func.sum(func.abs(CoinsLedgerModel.coins))
        ).filter(
            CoinsLedgerModel.user_id == user_id, CoinsLedgerModel.coins < 0
        ).scalar() or 0

        return UserManagementListItem(
            user_id=user.id,
            username=user.first_name or user.name or "Unknown",
            email=user.email or "",
            balance_coins=int(get_user_coin_balance(user_id)),
            no_of_agents=no_of_agents,
            no_of_phones=no_of_phones,
            last_active=format_time_ago(last_active) if last_active else "long time ago",
            is_suspended=bool(user.is_suspended),
            api_calls_total=calls_total,
            api_calls_monthly=calls_monthly,
            api_calls_weekly=calls_weekly,
            no_of_voices=no_of_voices,
            credits_consumed=int(credits_consumed),
            date_added=user.created_at.isoformat() if user.created_at else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user detail {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/transactions", response_model=PaginatedResponse[AdminUserTransactionItem], openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_transactions(user_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    """All coin-ledger transactions (added and deducted) for a user, newest first."""
    try:
        base = db.session.query(CoinsLedgerModel).filter(CoinsLedgerModel.user_id == user_id)
        total = base.count()
        rows = base.order_by(
            CoinsLedgerModel.created_at.desc(), CoinsLedgerModel.id.desc()
        ).offset((page - 1) * page_size).limit(page_size).all()

        action_map = {
            "debit_usage": "AI Interaction",
            "credit_subscription": "Subscription Credits",
            "credit_purchase": "Credits Purchased",
            "refund": "Refund",
            "expired": "Coins Expired",
            "carry_forward_reset": "Unused Coins Reset",
            "admin_adjustment": "Admin Adjustment",
        }

        items = []
        for item in rows:
            agent_name = None
            if item.reference_type == "conversation" and item.reference_id:
                conv = db.session.query(ConversationsModel).filter(ConversationsModel.id == item.reference_id).first()
                if conv and conv.agent:
                    agent_name = conv.agent.agent_name
            source_name = str(item.transaction_type.value if hasattr(item.transaction_type, "value") else item.transaction_type)
            items.append(AdminUserTransactionItem(
                date_time=item.created_at,
                action=action_map.get(source_name, source_name.replace("_", " ").title()),
                transaction_type=source_name,
                agent_name=agent_name,
                coins=item.coins,
                balance_before=item.balance_after - item.coins,
                balance_after=item.balance_after,
                reason=item.notes,
            ))

        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return PaginatedResponse(total=total, page=page, size=page_size, pages=total_pages, items=items)
    except Exception as e:
        logger.error(f"Error getting transactions for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/billing-history", response_model=PaginatedResponse[AdminUserBillingHistoryItem], openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_billing_history(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    provider_payment_id: Optional[str] = None,
    provider_order_id: Optional[str] = None,
):
    """
    Payment records (success/failed/pending/etc) for a user, newest first.

    provider_payment_id/provider_order_id let the webhook-events admin page
    drill down to the exact billing record a given webhook delivery is about.
    """
    try:
        base = db.session.query(PaymentModel).filter(PaymentModel.user_id == user_id)
        if provider_payment_id:
            base = base.filter(PaymentModel.provider_payment_id == provider_payment_id)
        if provider_order_id:
            base = base.filter(PaymentModel.provider_order_id == provider_order_id)
        total = base.count()
        rows = base.order_by(
            PaymentModel.created_at.desc(), PaymentModel.id.desc()
        ).offset((page - 1) * page_size).limit(page_size).all()

        items = []
        for p in rows:
            if p.payment_type in (PaymentTypeEnum.coin_purchase, PaymentTypeEnum.addon):
                coins = p.metadata_json.get("coins") if p.metadata_json else None
                description = f"Credit Purchase ({coins} credits)" if coins else "Credit Purchase"
            elif p.payment_type == PaymentTypeEnum.subscription:
                description = "Subscription Payment"
            else:
                description = "Miscellaneous Payment"

            items.append(AdminUserBillingHistoryItem(
                payment_id=p.id,
                date=p.created_at,
                description=description,
                amount=p.amount,
                currency=p.currency,
                status=p.status.value if hasattr(p.status, "value") else p.status,
                invoice_url=p.invoice_url,
                provider_payment_id=p.provider_payment_id,
                provider_order_id=p.provider_order_id,
            ))

        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return PaginatedResponse(total=total, page=page, size=page_size, pages=total_pages, items=items)
    except Exception as e:
        logger.error(f"Error getting billing history for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/billing-history/{payment_id}/invoice", openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_billing_invoice(user_id: int, payment_id: int):
    """Generates a PDF receipt for any user's payment, on demand — admin-only."""
    payment = db.session.query(PaymentModel).filter(
        PaymentModel.id == payment_id,
        PaymentModel.user_id == user_id,
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Invoice not found")

    user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == user_id).first()
    pdf_bytes = generate_invoice_pdf(payment, user)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="invoice-{payment.id}.pdf"'},
    )


@router.get("/users/{user_id}/agents-summary", response_model=List[AgentSummaryItem], openapi_extra={"security":[{"BearerAuth":[]}]})
def get_user_agents_summary(user_id: int):
    """Per-agent summary (web-agent/widget counts, conversation success/failed
    counts) for a specific user — for the admin user-detail Agents tab."""
    try:
        return build_agent_summaries(user_id)
    except Exception as e:
        logger.error(f"Error building agents summary for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/suspend",openapi_extra={"security":[{"BearerAuth":[]}]})
def suspend_user(user_id:int,request:SuspendUserRequest):
    try:
        user= (db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.id == user_id,
            UnifiedAuthModel.is_admin.is_(False)
        ).first())
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail= "user not found"
            )
        user.is_suspended = request.is_suspended
        if request.is_suspended:
            if request.reason:
                user.suspension_reason = request.reason
            #disable agents for the user
            widgets = user.widgets
            for widget in widgets:
                widget.is_enabled = False
        else:
            user.suspension_reason = None
            widgets = user.widgets
            for widget in widgets:
                widget.is_enabled = True
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)
        return {"message":f"User {'suspended' if request.is_suspended else 'unsuspend'} successfully"}
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error suspending user: {str(e)}")
        raise HTTPException(status_code=500,detail=str(e))

@router.post("/users/{user_id}/adjust-coins", openapi_extra={"security": [{"BearerAuth": []}]})
def adjust_user_coins(user_id: int, request: AdjustUserCoinRequest):
    """
    Adjust user coins (add or deduct) by admin.
    Positive amount adds coins, negative amount deducts coins.
    """
    try:
        user = (db.session.query(UnifiedAuthModel).filter(
            UnifiedAuthModel.id == user_id,
            UnifiedAuthModel.is_admin.is_(False)
        ).first())
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        if user.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This user's account is suspended. Please reactivate the account before adjusting coins."
            )

        success = admin_adjust_coins(
            user_id=user_id,
            amount=request.coins,
            reason=request.reason,
        )
        
        if not success and request.coins < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to adjust coins. Check if user has sufficient balance for deduction."
            )
            
        return {"message": "Coins adjusted successfully", "new_balance": get_user_coin_balance(user_id)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adjusting coins for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

