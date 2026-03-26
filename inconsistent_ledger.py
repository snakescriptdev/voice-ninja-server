"""
One-time script to fix inconsistent `balance_after` in coins_ledger table.
Rebuilds balance using `coins` as source of truth.
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from app_v2.core.config import VoiceSettings
from app_v2.databases.models import CoinsLedgerModel


def rebuild_balance(user_id: int, session: Session):
    """
    Rebuild balance_after for a given user using running sum of coins.
    """

    entries = session.execute(
        select(CoinsLedgerModel)
        .where(CoinsLedgerModel.user_id == user_id)
        .order_by(
            CoinsLedgerModel.created_at.asc(),
            CoinsLedgerModel.id.asc()  # ✅ ensures deterministic order
        )
    ).scalars().all()

    running_balance = 0

    for entry in entries:
        running_balance += entry.coins
        entry.balance_after = running_balance

    # ✅ sanity check
    if entries:
        expected_balance = sum(e.coins for e in entries)
        if entries[-1].balance_after != expected_balance:
            raise Exception(f"Ledger mismatch for user {user_id}")


def make_all_balances_correct(session: Session):
    """
    Rebuild balance_after for all users safely.
    """

    users = session.execute(
        select(CoinsLedgerModel.user_id).distinct()
    ).scalars().all()

    for user_id in users:
        try:
            rebuild_balance(user_id, session)
            session.commit()  # ✅ commit per user (safe checkpoint)
            print(f"✅ Fixed user {user_id}")

        except Exception as e:
            session.rollback()
            print(f"❌ Failed for user {user_id}: {e}")


if __name__ == "__main__":
    engine = create_engine(VoiceSettings.DB_URL)

    SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine
    )

    session = SessionLocal()

    try:
        make_all_balances_correct(session)
    finally:
        session.close()