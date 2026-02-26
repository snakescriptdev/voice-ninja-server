from fastapi_sqlalchemy import db
from sqlalchemy import func
from app_v2.databases.models import CoinsLedgerModel, CoinTransactionTypeEnum
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

def get_user_coin_balance(user_id: int) -> int:
    """
    Returns the current coin balance for a user.
    It calculates the sum of all coin transactions in the ledger.
    Must be called within an active db() session block.
    """
    try:
        result = db.session.query(func.sum(CoinsLedgerModel.coins)).filter(
            CoinsLedgerModel.user_id == user_id
        ).scalar()
        return result or 0
    except Exception as e:
        logger.error(f"Failed to get coin balance for user {user_id}: {e}")
        return 0

def deduct_coins(user_id: int, amount: int, reference_type: str = None, reference_id: int = None, commit: bool = True) -> bool:
    """
    Deducts coins from the user's ledger and updates the balance.
    amount should be a positive integer representing the usage cost.
    Must be called within an active db() session block.
    """
    if amount <= 0:
        raise Exception("Amount should be greater than 0")
        
    try:
        current_balance = get_user_coin_balance(user_id)
        
        ledger_entry = CoinsLedgerModel(
            user_id=user_id,
            transaction_type=CoinTransactionTypeEnum.debit_usage,
            coins=-amount,
            reference_type=reference_type,
            reference_id=reference_id,
            balance_after=current_balance - amount
        )
        db.session.add(ledger_entry)
        
        if commit:
            db.session.commit()
            
        logger.info(f"Deducted {amount} coins from user {user_id}. New balance: {current_balance - amount}")
        return True
    except Exception as e:
        logger.error(f"Failed to deduct {amount} coins from user {user_id}: {e}")
        if commit:
            db.session.rollback()
        return False
