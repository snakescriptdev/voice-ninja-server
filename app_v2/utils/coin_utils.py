from fastapi_sqlalchemy import db
from sqlalchemy import func
from app_v2.databases.models import CoinsLedgerModel, CoinTransactionTypeEnum
from app_v2.core.logger import setup_logger
from sqlalchemy import or_
from datetime import datetime

logger = setup_logger(__name__)

def get_user_coin_balance(user_id: int) -> int:
    """
    Returns the current coin balance for a user based on valid credit batches.
    Sum of remaining_coins where not expired.
    Must be called within an active db() session block.
    """
    try:
        now = datetime.utcnow()
        result = db.session.query(func.sum(CoinsLedgerModel.remaining_coins)).filter(
            CoinsLedgerModel.user_id == user_id,
            CoinsLedgerModel.remaining_coins > 0,
            or_(
                CoinsLedgerModel.expiry_at == None,
                CoinsLedgerModel.expiry_at > now
            )
        ).scalar()
        return result or 0
    except Exception as e:
        logger.error(f"Failed to get coin balance for user {user_id}: {e}")
        return 0

def deduct_coins(user_id: int, amount: float | int, reference_type: str = None, reference_id: int = None, commit: bool = True, transaction_type: CoinTransactionTypeEnum = CoinTransactionTypeEnum.debit_usage) -> bool:
    """
    Deducts coins from the user's ledger using FIFO logic on valid credit batches.
    amount is treated as the raw coin count.
    Must be called within an active db() session block.
    """
    coin_amount = int(amount)

    if coin_amount <= 0:
        if amount > 0: # If it was a small float > 0, deduct at least 1 coin
             coin_amount = 1
        else:
            logger.info(f"Skipping deduction for 0 or negative amount: {amount}")
            return True
        
    try:
        now = datetime.utcnow()
        # 1. Fetch valid credit batches FIFO with row-level locking
        batches = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == user_id,
            CoinsLedgerModel.remaining_coins > 0,
            or_(
                CoinsLedgerModel.expiry_at == None,
                CoinsLedgerModel.expiry_at > now
            )
        ).order_by(CoinsLedgerModel.created_at.asc()).with_for_update().all()
        
        total_available = sum(b.remaining_coins for b in batches)
        if total_available < coin_amount:
            logger.warning(f"Insufficient coins for user {user_id}. Needed: {coin_amount}, Available: {total_available}")
            return False
            
        remaining_to_deduct = coin_amount
        for batch in batches:
            if remaining_to_deduct <= 0:
                break
                
            if batch.remaining_coins >= remaining_to_deduct:
                batch.remaining_coins -= remaining_to_deduct
                remaining_to_deduct = 0
            else:
                remaining_to_deduct -= batch.remaining_coins
                batch.remaining_coins = 0
        
        # 2. Create debit entry
        current_balance = total_available - coin_amount
        ledger_entry = CoinsLedgerModel(
            user_id=user_id,
            transaction_type=transaction_type,
            coins=-coin_amount,
            reference_type=reference_type,
            reference_id=reference_id,
            balance_after=current_balance,
            remaining_coins=0
        )
        db.session.add(ledger_entry)
        
        if commit:
            db.session.commit()
            
        logger.info(f"Deducted {coin_amount} coins from user {user_id}. New balance: {current_balance}")
        return True
    except Exception as e:
        logger.error(f"Failed to deduct {coin_amount} coins from user {user_id}: {e}")
        if commit:
            db.session.rollback()
        return False

def reset_unused_subscription_coins(user_id: int):
    """
    Zeros out remaining coins for all subscription-related credit batches for the user.
    Creates an 'carry_forward_reset' ledger entry for the total reset amount.
    """
    try:
        # Find all subscription credits with remaining coins
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
            current_balance = get_user_coin_balance(user_id) # Should reflect the reset now
            ledger_entry = CoinsLedgerModel(
                user_id=user_id,
                transaction_type=CoinTransactionTypeEnum.carry_forward_reset,
                coins=-total_reset,
                reference_type="carry_forward_reset",
                balance_after=current_balance,
                remaining_coins=0
            )
            db.session.add(ledger_entry)
            logger.info(f"Reset {total_reset} subscription coins for user {user_id} due to non-carry-forward policy.")
            return total_reset
        return 0
    except Exception as e:
        logger.error(f"Failed to reset subscription coins for user {user_id}: {e}")
        return 0

def expire_user_coins(user_id: int):
    """
    Finds all credit batches that have expired but still have remaining coins for a specific user.
    Zeros them out and creates 'expired' ledger entries.
    """
    try:
        now = datetime.utcnow()
        expired_batches = db.session.query(CoinsLedgerModel).filter(
            CoinsLedgerModel.user_id == user_id,
            CoinsLedgerModel.remaining_coins > 0,
            CoinsLedgerModel.expiry_at != None,
            CoinsLedgerModel.expiry_at <= now
        ).all()
        
        total_expired = 0
        for batch in expired_batches:
            total_expired += batch.remaining_coins
            batch.remaining_coins = 0
            
        if total_expired > 0:
            current_balance = get_user_coin_balance(user_id)
            ledger_entry = CoinsLedgerModel(
                user_id=user_id,
                transaction_type=CoinTransactionTypeEnum.expired,
                coins=-total_expired,
                reference_type="expiry",
                balance_after=current_balance,
                remaining_coins=0
            )
            db.session.add(ledger_entry)
            logger.info(f"Expired {total_expired} coins for user {user_id}.")
            return total_expired
        return 0
    except Exception as e:
        logger.error(f"Failed to expire coins for user {user_id}: {e}")
        return 0

def run_expiry_check():
    """
    Global expiry check for all users. Ideally run via a background task.
    """
    try:
        now = datetime.utcnow()
        # Find all users with expired coins
        expired_users = db.session.query(CoinsLedgerModel.user_id).filter(
            CoinsLedgerModel.remaining_coins > 0,
            CoinsLedgerModel.expiry_at != None,
            CoinsLedgerModel.expiry_at <= now
        ).distinct().all()
        
        for (user_id,) in expired_users:
            expire_user_coins(user_id)
            
        db.session.commit()
        logger.info("Global expiry check completed successfully.")
    except Exception as e:
        logger.error(f"Failed to run global expiry check: {e}")
        db.session.rollback()


def admin_adjust_coins(user_id: int, amount: int, reason: str, commit: bool = True) -> bool:
    """
    Adjusts user coins (add or deduct) from admin management.
    amount > 0 adds coins (credit), amount < 0 deducts coins (debit).
    Must be called within an active db() session block.
    """
    if amount == 0:
        return True
    
    try:
        now = datetime.utcnow()
        current_balance = get_user_coin_balance(user_id)
        
        if amount > 0:
            # Credit logic
            ledger_entry = CoinsLedgerModel(
                user_id=user_id,
                transaction_type=CoinTransactionTypeEnum.admin_adjustment,
                coins=amount,
                remaining_coins=amount,
                reference_type="admin_adjustment",
                reference_id=None,
                balance_after=current_balance + amount,
                created_at=now
            )
            db.session.add(ledger_entry)
            if commit:
                db.session.commit()
            logger.info(f"Admin added {amount} coins to user {user_id}. Reason: {reason}")
            return True
        else:
            # Debit logic (negative amount)
            # Reuse logic from deduct_coins but with admin_adjustment type
            return deduct_coins(
                user_id=user_id, 
                amount=abs(amount), 
                reference_type="admin_adjustment", 
                commit=commit, 
                transaction_type=CoinTransactionTypeEnum.admin_adjustment
            )

    except Exception as e:
        import traceback
        logger.error(f"Failed admin coin adjustment for user {user_id}: {e}\n{traceback.format_exc()}")
        if commit:
            db.session.rollback()
        return False
