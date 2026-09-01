from fastapi_sqlalchemy import db
from sqlalchemy import func, desc
from app_v2.databases.models import (
    CoinsLedgerModel,
    CoinTransactionTypeEnum,
    CoinUsageSettingsModel,
    ConversationsModel,
    PaymentModel,
    PaymentStatusEnum,
    UnifiedAuthModel,
    AIModels,
    VoiceModel,
)
from app_v2.core.logger import setup_logger
from datetime import datetime, timezone
import math

logger = setup_logger(__name__)


def get_credits_per_rupee(conversation: "ConversationsModel") -> float | None:
    """
    Returns the credits-per-rupee conversion rate to use for a conversation's
    INR cost — the rate snapshotted on its settings_version, i.e. the rate
    that was actually in effect when it happened, not today's rate (which may
    have since changed). Falls back to the live CoinUsageSettingsModel rate
    only for legacy conversations that predate rate snapshotting (no
    settings_version_id, or a version row saved before this field existed).

    A conversation is required — there is no "current rate" concept here.
    Code that needs today's live rate for something other than a specific
    conversation's cost (e.g. a balance notification, or the SQL-level
    fallback in a bulk aggregate) should read
    CoinUsageSettingsModel.get_settings().credits_per_rupee directly instead.

    Must be called within an active db() session block.
    """
    settings_version = getattr(conversation, "settings_version", None)
    version_rate = getattr(settings_version, "credits_per_rupee", None)
    if version_rate:
        return version_rate

    coin_setting_record = (
        db.session.query(CoinUsageSettingsModel)
        .order_by(desc(CoinUsageSettingsModel.id))
        .first()
    )
    return coin_setting_record.credits_per_rupee if coin_setting_record else None


def coins_to_inr(coins: float, credits_per_rupee: float | None) -> float:
    """Converts a raw coin/credit amount into a user-facing INR amount."""
    if not credits_per_rupee or credits_per_rupee <= 0:
        return 0.0
    return round(float(coins) / credits_per_rupee, 2)


def apply_banner_rearm(dismissed: bool, recovered: bool, condition_active: bool):
    """
    Given a low-X-banner's persisted dismissal state and whether its trigger
    condition is true right now, decide whether to show it — and whether the
    persisted state needs to change.

    Once dismissed, a banner stays hidden through the SAME low episode. If the
    condition clears (condition_active goes False), we mark it `recovered`.
    The next time the condition becomes True again after that, it's a NEW
    episode, so the dismissal is cleared (re-armed) and the banner shows again
    — instead of staying hidden forever after one click.

    Shared by both the per-user coin-balance banners (coin_purchase.py) and
    the global ElevenLabs-credits admin banner (admin_dashboard.py) — the
    caller decides what row the persisted (dismissed, recovered) state lives
    on (per-user vs. a single shared settings row).
    """
    if not dismissed:
        return False, False, condition_active
    if condition_active:
        if recovered:
            return False, False, True
        return True, False, False
    return True, True, False


def get_user_coin_balance(user_id: int) -> int:
    """
    Returns the current coin balance for a user by reading balance_after
    from the most recent ledger entry.

    Returns 0 if the user has no ledger entries yet.
    Returns negative values when the user is in debt (post-call overdraft).

    Must be called within an active db() session block.
    """
    try:
        latest = (
            db.session.query(CoinsLedgerModel.balance_after)
            .filter(CoinsLedgerModel.user_id == user_id)
            .order_by(CoinsLedgerModel.created_at.desc(),CoinsLedgerModel.id.desc())
            .first()
        )
        return latest[0] if latest else 0
    except Exception as e:
        logger.error(f"Failed to get coin balance for user {user_id}: {e}")
        return 0


def deduct_coins(
    user_id: int,
    amount: float | int,
    reference_type: str = None,
    reference_id: int = None,
    commit: bool = True,
    transaction_type: CoinTransactionTypeEnum = CoinTransactionTypeEnum.debit_usage,
    force: bool = False,
    notes: str | None = None,
) -> bool:
    """
    Deducts coins from the user's ledger using FIFO logic on valid credit batches.
    amount is treated as the raw coin count.

    force=True: deducts as much as is available, capping at the user's current
                balance instead of refusing outright — used for post-call billing,
                where the call already happened and some deduction must be recorded
                even if the real cost exceeds what's left. Never pushes the balance
                negative; any shortfall beyond the available balance is absorbed
                rather than recorded as user debt.
    force=False (default): refuses and returns False if balance is insufficient.

    Must be called within an active db() session block.
    """
    # if amount is 0 or negative, return True
    if amount <= 0:
        return True
    # convert amount to integer
    coin_amount = math.ceil(amount)

    try:
        # 1. Fetch valid credit batches FIFO with row-level locking. Credits
        # never expire, so every batch with coins left is eligible.
        batches = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == user_id,
            CoinsLedgerModel.remaining_coins > 0,
        ).order_by(CoinsLedgerModel.created_at.asc()).with_for_update().all()
        #latest balance row
        latest_balance_row = (
            db.session.query(CoinsLedgerModel.balance_after)
            .filter(CoinsLedgerModel.user_id == user_id)
            .order_by(CoinsLedgerModel.created_at.desc())
            .with_for_update()
            .first()
        )
        current_balance = latest_balance_row[0] if latest_balance_row else 0

        total_available = sum(b.remaining_coins for b in batches)

        if total_available < coin_amount:
            if not force:
                logger.warning(
                    f"Insufficient coins for user {user_id}. "
                    f"Needed: {coin_amount}, Available: {total_available}"
                )
                return False
            # force=True: cap the deduction at what's actually available —
            # the shortfall is absorbed (not billed) rather than putting the
            # user in debt. Balance floors at 0, never negative.
            shortfall = coin_amount - total_available
            logger.warning(
                f"Post-call balance shortfall for user {user_id}: "
                f"cost={coin_amount}, available={total_available}, "
                f"shortfall={shortfall} absorbed — balance capped at 0"
            )
            coin_amount = total_available

        if coin_amount <= 0:
            # Nothing left to deduct (already fully drained) — no-op rather
            # than writing a zero-amount ledger entry.
            if commit:
                db.session.commit()
            return True

        # 2. Drain available credit batches for the (possibly capped) amount.
        remaining_to_deduct = coin_amount
        for batch in batches:
            if remaining_to_deduct <= 0:
                break
            deduct_from_batch = min(batch.remaining_coins, remaining_to_deduct)
            batch.remaining_coins -= deduct_from_batch
            remaining_to_deduct -= deduct_from_batch

        # 3. Create debit entry — balance_after reflects the (possibly capped)
        #    amount actually deducted, never going below 0.
        balance_after = current_balance - coin_amount
        ledger_entry = CoinsLedgerModel(
            user_id=user_id,
            transaction_type=transaction_type,
            coins=-coin_amount,
            reference_type=reference_type,
            reference_id=reference_id,
            balance_after=balance_after,
            remaining_coins=0,
            notes=notes,
        )
        db.session.add(ledger_entry)

        if commit:
            db.session.commit()

        logger.info(
            f"Deducted {coin_amount} coins from user {user_id}. "
            f"New balance: {balance_after}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to deduct {coin_amount} coins from user {user_id}: {e}")
        if commit:
            db.session.rollback()
        return False


def reset_unused_subscription_coins(user_id: int):
    """
    Zeros out remaining coins for all subscription-related credit batches for the user.
    Creates a 'carry_forward_reset' ledger entry for the total reset amount.
    """
    try:
        subscription_batches = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == user_id,
            CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.credit_subscription,
            CoinsLedgerModel.remaining_coins > 0
        ).all()

        total_reset = 0
        for batch in subscription_batches:
            total_reset += batch.remaining_coins
            batch.remaining_coins = 0

        if total_reset > 0:
            current_balance = get_user_coin_balance(user_id)
            balance_after = current_balance - total_reset
            ledger_entry = CoinsLedgerModel(
                user_id=user_id,
                transaction_type=CoinTransactionTypeEnum.carry_forward_reset,
                coins=-total_reset,
                reference_type="carry_forward_reset",
                balance_after=balance_after,
                remaining_coins=0,
            )
            db.session.add(ledger_entry)
            db.session.commit()
            logger.info(
                f"Reset {total_reset} subscription coins for user {user_id} "
                f"due to non-carry-forward policy."
            )
            return total_reset
        return 0
    except Exception as e:
        logger.error(f"Failed to reset subscription coins for user {user_id}: {e}")
        return 0


ADMIN_MAX_COINS_ADDED_PER_DAY = 10000

def get_admin_added_coins_today(user_id: int) -> int:
    """Sum of coins already manually credited to this user today (UTC) via
    admin_adjust_coins — used to enforce a daily cap on admin top-ups."""
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.session.query(func.coalesce(func.sum(CoinsLedgerModel.coins), 0)).filter(
        CoinsLedgerModel.user_id == user_id,
        CoinsLedgerModel.transaction_type == CoinTransactionTypeEnum.admin_adjustment,
        CoinsLedgerModel.coins > 0,
        CoinsLedgerModel.created_at >= day_start,
    ).scalar()
    return int(total or 0)


def admin_adjust_coins(
    user_id: int,
    amount: int,
    reason: str,
    commit: bool = True,
) -> bool:
    """
    Adjusts user coins (add or deduct) from admin management.
    amount > 0 adds coins (credit), amount < 0 deducts coins (debit).
    Must be called within an active db() session block.
    """
    if amount == 0:
        return True

    try:
        now = datetime.now(timezone.utc)
        current_balance = get_user_coin_balance(user_id)
        if  amount<0 and current_balance<abs(amount):
            return False
        if amount > 0:
            ledger_entry = CoinsLedgerModel(
                user_id=user_id,
                transaction_type=CoinTransactionTypeEnum.admin_adjustment,
                coins=amount,
                remaining_coins=amount,
                reference_type="admin_adjustment",
                reference_id=None,
                balance_after=current_balance + amount,
                created_at=now,
                notes=reason,
            )
            db.session.add(ledger_entry)
            if commit:
                db.session.commit()
            logger.info(
                f"Admin added {amount} coins to user {user_id}. Reason: {reason}"
            )
            return True
        else:
            # Debit logic (negative amount)
            return deduct_coins(
                user_id=user_id,
                amount=abs(amount),
                reference_type="admin_adjustment",
                commit=commit,
                transaction_type=CoinTransactionTypeEnum.admin_adjustment,
                notes=reason,
            )

    except Exception as e:
        import traceback
        logger.error(
            f"Failed admin coin adjustment for user {user_id}: {e}\n{traceback.format_exc()}"
        )
        if commit:
            db.session.rollback()
        return False


def grant_signup_credit(user_id: int, commit: bool = True) -> bool:
    """
    One-time free INR credit grant for a brand-new signup, converted to coins
    via CoinUsageSettingsModel.credits_per_rupee. Idempotent via
    UnifiedAuthModel.signup_credit_granted — safe to call multiple times, and
    must only ever be invoked on true first-time signup (not re-login).

    If the configured signup_free_credit_inr is <= 0, the user is still
    marked signup_credit_granted so a later increase to the setting doesn't
    retroactively re-trigger a grant for already-processed users.

    Must be called within an active db() session block.
    """
    try:
        user = db.session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == user_id).first()
        if not user:
            logger.error(f"grant_signup_credit: user {user_id} not found")
            return False
        if user.signup_credit_granted:
            return True

        settings = CoinUsageSettingsModel.get_settings()
        inr_amount = settings.signup_free_credit_inr if settings else 0

        if not inr_amount or inr_amount <= 0:
            user.signup_credit_granted = True
            if commit:
                db.session.commit()
            return True

        credits_per_rupee = settings.credits_per_rupee or 1.0
        coins_amount = int(round(inr_amount * credits_per_rupee))

        if coins_amount > 0:
            current_balance = get_user_coin_balance(user_id)
            ledger_entry = CoinsLedgerModel(
                user_id=user_id,
                transaction_type=CoinTransactionTypeEnum.signup_bonus,
                coins=coins_amount,
                remaining_coins=coins_amount,
                reference_type="signup_bonus",
                reference_id=None,
                balance_after=current_balance + coins_amount,
                created_at=datetime.now(timezone.utc),
                notes=f"Free signup credit: ₹{inr_amount}",
            )
            db.session.add(ledger_entry)

        user.signup_credit_granted = True
        if commit:
            db.session.commit()
        logger.info(f"Granted signup credit to user {user_id}: ₹{inr_amount} ({coins_amount} coins)")
        return True
    except Exception as e:
        import traceback
        logger.error(
            f"Failed to grant signup credit for user {user_id}: {e}\n{traceback.format_exc()}"
        )
        if commit:
            db.session.rollback()
        return False


def user_has_successful_payment(user_id: int) -> bool:
    """
    True once this user has at least one PaymentModel row with
    status == success — independent of remaining coin balance. This is a
    one-way, permanent unlock: once true, always true, even if the balance is
    later driven to 0.

    Must be called within an active db() session block.
    """
    return (
        db.session.query(PaymentModel.id)
        .filter(
            PaymentModel.user_id == user_id,
            PaymentModel.status == PaymentStatusEnum.success,
        )
        .first()
        is not None
    )


def get_free_tier_defaults() -> tuple["AIModels | None", "VoiceModel | None"]:
    """
    Returns (free_tier_model, free_tier_voice) — the admin-designated
    free-tier default AI model and voice, or None for either if not yet
    configured. Voice lookup is scoped to user_id IS NULL (system voices
    only) — a user's own custom voice can never be the global default.

    Callers should treat an unconfigured default (None) as "gate is a no-op"
    — never lock a user out before an admin has actually configured this.

    Must be called within an active db() session block.
    """
    free_model = (
        db.session.query(AIModels)
        .filter(AIModels.is_free_tier_default == True)  # noqa: E712
        .first()
    )
    free_voice = (
        db.session.query(VoiceModel)
        .filter(
            VoiceModel.is_free_tier_default == True,  # noqa: E712
            VoiceModel.user_id.is_(None),
        )
        .first()
    )
    return free_model, free_voice