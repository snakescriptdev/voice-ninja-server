from datetime import datetime, timezone, timedelta

from fastapi_sqlalchemy import db
from sqlalchemy import desc

from app_v2.databases.models import (
    CoinsLedgerModel,
    PaymentModel,
    PlanModel,
    UserSubscriptionModel,
)
from app_v2.schemas.enum_types import (
    BillingPeriodEnum,
    CoinTransactionTypeEnum,
    PaymentStatusEnum,
    PaymentTypeEnum,
    SubscriptionStatusEnum,
)
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

_FREE_PROVIDER = "free"


def grant_free_plan_on_signup(user_id: int) -> None:
    """
    Grants the active plan with the highest coin count to a new user at no cost.
    Replicates the DB writes performed during a successful subscription payment:
      - UserSubscriptionModel (status=active)
      - PaymentModel (amount=0, status=success)
      - CoinsLedgerModel (credit_subscription)
    Safe to call for both OTP and Google OAuth signups.
    """
    try:
        # --- 1. Fetch the best plan ---
        plan_id = plan_coins = plan_carry_forward = None
        plan_currency = "INR"
        plan_billing_period = plan_name = None

        with db():
            plan = (
                db.session.query(PlanModel)
                .filter(PlanModel.is_active == True, PlanModel.is_deleted == False)
                .order_by(desc(PlanModel.coins_included))
                .first()
            )
            if not plan:
                logger.warning(
                    f"grant_free_plan_on_signup: no active plan found, skipping for user {user_id}"
                )
                return
            plan_id = plan.id
            plan_coins = plan.coins_included
            plan_currency = plan.currency
            plan_billing_period = plan.billing_period
            plan_carry_forward = plan.carry_forward_coins
            plan_name = plan.display_name

        now = datetime.now(timezone.utc)
        period_end = now + (timedelta(days=365) if plan_billing_period == BillingPeriodEnum.annual else timedelta(days=30))

        # --- 2. Create subscription row ---
        with db():
            subscription = UserSubscriptionModel(
                user_id=user_id,
                plan_id=plan_id,
                status=SubscriptionStatusEnum.active,
                current_period_start=now,
                current_period_end=period_end,
                cancel_at_period_end=False,
                provider=_FREE_PROVIDER,
                provider_subscription_id=f"free_signup_{user_id}",
                subscription_metadata={"source": "signup_bonus"},
            )
            db.session.add(subscription)
            db.session.commit()

        # --- 3. Create zero-amount payment record ---
        payment_id = None
        with db():
            payment = PaymentModel(
                user_id=user_id,
                amount=0,
                currency=plan_currency,
                status=PaymentStatusEnum.success,
                provider=None,
                payment_type=PaymentTypeEnum.subscription,
                metadata_json={"plan_id": plan_id, "source": "signup_bonus"},
            )
            db.session.add(payment)
            db.session.commit()
            db.session.refresh(payment)
            payment_id = payment.id

        # --- 4. Credit coins to ledger ---
        with db():
            coin_entry = CoinsLedgerModel(
                user_id=user_id,
                transaction_type=CoinTransactionTypeEnum.credit_subscription,
                coins=plan_coins,
                remaining_coins=plan_coins,
                expiry_at=period_end,
                reference_type="payment",
                reference_id=payment_id,
                balance_after=plan_coins,
            )
            db.session.add(coin_entry)
            db.session.commit()

        logger.info(
            f"grant_free_plan_on_signup: granted '{plan_name}' ({plan_coins} coins) to user {user_id}"
        )

    except Exception as e:
        logger.error(f"grant_free_plan_on_signup: failed for user {user_id}: {e}")
