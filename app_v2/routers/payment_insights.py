from fastapi import APIRouter, Depends, HTTPException
from fastapi_sqlalchemy import db
from sqlalchemy import func, desc, and_
from datetime import datetime, timedelta
from typing import List

from app_v2.utils.jwt_utils import HTTPBearer,is_admin
from app_v2.databases.models import UnifiedAuthModel, PaymentModel, PlanModel
from app_v2.schemas.payment_insights_schema import (
    PaymentInsightsResponse, 
    DailyTrendItem, 
    RevenueItem, 
    PaymentItemSchema
)
from app_v2.schemas.enum_types import PaymentStatusEnum, PaymentTypeEnum

security = HTTPBearer()
router = APIRouter(prefix="/api/v2/admin/payments/insights", tags=["Admin Payment Insights"],dependencies=[Depends(is_admin)],)

@router.get("", response_model=PaymentInsightsResponse,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_payment_insights():
    """
    Fetch comprehensive payment insights for the admin dashboard.
    """

    try:
        # Time ranges
        now = datetime.utcnow()
        first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        seven_days_ago = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. Basic Metrics
        total_revenue_all_time = db.session.query(func.sum(PaymentModel.amount)).filter(
            PaymentModel.status == PaymentStatusEnum.success
        ).scalar() or 0.0

        total_revenue_monthly = db.session.query(func.sum(PaymentModel.amount)).filter(
            PaymentModel.status == PaymentStatusEnum.success,
            PaymentModel.created_at >= first_day_of_month
        ).scalar() or 0.0

        successful_count_all_time = db.session.query(func.count(PaymentModel.id)).filter(
            PaymentModel.status == PaymentStatusEnum.success
        ).scalar() or 0

        successful_count_monthly = db.session.query(func.count(PaymentModel.id)).filter(
            PaymentModel.status == PaymentStatusEnum.success,
            PaymentModel.created_at >= first_day_of_month
        ).scalar() or 0

        failed_count_all_time = db.session.query(func.count(PaymentModel.id)).filter(
            PaymentModel.status == PaymentStatusEnum.failed
        ).scalar() or 0

        failed_count_monthly = db.session.query(func.count(PaymentModel.id)).filter(
            PaymentModel.status == PaymentStatusEnum.failed,
            PaymentModel.created_at >= first_day_of_month
        ).scalar() or 0

        # 2. Daily Trends (Last 7 Days)
        daily_trends_query = db.session.query(
            func.date(PaymentModel.created_at).label('date'),
            func.sum(PaymentModel.amount).label('revenue')
        ).filter(
            PaymentModel.status == PaymentStatusEnum.success,
            PaymentModel.created_at >= seven_days_ago
        ).group_by(func.date(PaymentModel.created_at)).all()

        trends_map = {str(item.date): float(item.revenue) for item in daily_trends_query}
        daily_revenue_trend = []
        for i in range(7):
            day = (seven_days_ago + timedelta(days=i)).date()
            day_str = str(day)
            daily_revenue_trend.append(DailyTrendItem(
                date=day_str,
                revenue=trends_map.get(day_str, 0.0)
            ))

        # 3. Revenue by Plan & Coin Bundle
        all_success_payments = db.session.query(PaymentModel).filter(
            PaymentModel.status == PaymentStatusEnum.success
        ).all()

        plan_revenue_map = {}
        bundle_revenue_map = {}
        
        # Pre-fetch plans and bundles for names
        plans = {p.id: p.display_name for p in db.session.query(PlanModel).all()}
        from app_v2.databases.models import CoinPackageModel
        bundles = {b.id: b.name for b in db.session.query(CoinPackageModel).all()}

        for p in all_success_payments:
            if p.payment_type == PaymentTypeEnum.subscription:
                p_id = p.metadata_json.get("plan_id") if p.metadata_json else None
                name = plans.get(p_id, "Unknown Plan")
                plan_revenue_map[name] = plan_revenue_map.get(name, 0.0) + p.amount
            elif p.payment_type == PaymentTypeEnum.coin_purchase or p.payment_type == PaymentTypeEnum.addon:
                b_id = p.metadata_json.get("bundle_id") if p.metadata_json else None
                name = bundles.get(b_id, "Unknown Bundle")
                bundle_revenue_map[name] = bundle_revenue_map.get(name, 0.0) + p.amount

        revenue_by_plan = [RevenueItem(name=k, revenue=v) for k, v in plan_revenue_map.items()]
        revenue_by_coin_bundle = [RevenueItem(name=k, revenue=v) for k, v in bundle_revenue_map.items()]

        # 4. Recent Transactions
        from app_v2.databases.models import UnifiedAuthModel
        recent_txs = db.session.query(PaymentModel, UnifiedAuthModel.username).join(
            UnifiedAuthModel, PaymentModel.user_id == UnifiedAuthModel.id
        ).filter(
            PaymentModel.status == PaymentStatusEnum.success
        ).order_by(desc(PaymentModel.created_at)).limit(10).all()

        recent_transactions = []
        for p, uname in recent_txs:
            plan_name = None
            if p.payment_type == PaymentTypeEnum.subscription:
                p_id = p.metadata_json.get("plan_id") if p.metadata_json else None
                plan_name = plans.get(p_id)
            elif p.payment_type == PaymentTypeEnum.coin_purchase or p.payment_type == PaymentTypeEnum.addon:
                b_id = p.metadata_json.get("bundle_id") if p.metadata_json else None
                plan_name = bundles.get(b_id)
            
            recent_transactions.append(PaymentItemSchema(
                id=p.id,
                user_id=p.user_id,
                user_name=uname,
                amount=p.amount,
                currency=p.currency,
                status=p.status,
                payment_type=p.payment_type,
                created_at=p.created_at,
                plan_name=plan_name
            ))

        # 5. Recent Failed Payments
        recent_failed = db.session.query(PaymentModel, UnifiedAuthModel.username).join(
            UnifiedAuthModel, PaymentModel.user_id == UnifiedAuthModel.id
        ).filter(
            PaymentModel.status == PaymentStatusEnum.failed
        ).order_by(desc(PaymentModel.created_at)).limit(10).all()

        recent_failed_payments = []
        for p, uname in recent_failed:
            recent_failed_payments.append(PaymentItemSchema(
                id=p.id,
                user_id=p.user_id,
                user_name=uname,
                amount=p.amount,
                currency=p.currency,
                status=p.status,
                payment_type=p.payment_type,
                created_at=p.created_at
            ))

        return PaymentInsightsResponse(
            total_revenue_all_time=total_revenue_all_time,
            total_revenue_monthly=total_revenue_monthly,
            successful_payments_count_all_time=successful_count_all_time,
            successful_payments_count_monthly=successful_count_monthly,
            failed_payments_count_all_time=failed_count_all_time,
            failed_payments_count_monthly=failed_count_monthly,
            daily_revenue_trend=daily_revenue_trend,
            revenue_by_plan=revenue_by_plan,
            revenue_by_coin_bundle=revenue_by_coin_bundle,
            recent_transactions=recent_transactions,
            recent_failed_payments=recent_failed_payments
        )

    except Exception as e:
        db.session.rollback()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
