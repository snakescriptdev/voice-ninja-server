"""
One-time script to fully rebuild coins_ledger consistency:
- Recomputes balance_after
- Recomputes remaining_coins using FIFO logic
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from collections import deque
from datetime import datetime
from app_v2.core.config import VoiceSettings
from app_v2.databases.models import CoinsLedgerModel


def rebuild_user_ledger(user_id, session):
    # Fetch all entries in correct deterministic order
    entries = session.execute(
        select(CoinsLedgerModel)
        .where(CoinsLedgerModel.user_id == user_id)
        .order_by(
            CoinsLedgerModel.created_at.asc(),
            CoinsLedgerModel.id.asc()
        )
    ).scalars().all()

    running_balance = 0

    # FIFO queue → (entry_id, remaining_coins_ref)
    fifo_queue = deque()

    for entry in entries:
        coins = entry.coins or 0

        # --- CREDIT ---
        if coins > 0:
            entry.remaining_coins = coins
            fifo_queue.append(entry)

        # --- DEBIT ---
        elif coins < 0:
            to_deduct = abs(coins)

            while to_deduct > 0 and fifo_queue:
                credit_entry = fifo_queue[0]

                available = credit_entry.remaining_coins

                if available <= to_deduct:
                    to_deduct -= available
                    credit_entry.remaining_coins = 0
                    fifo_queue.popleft()
                else:
                    credit_entry.remaining_coins -= to_deduct
                    to_deduct = 0

            # If still left → overdraft (negative balance case)
            # remaining_coins already 0 for debit entries
            entry.remaining_coins = 0

        # --- UPDATE BALANCE ---
        running_balance += coins
        entry.balance_after = running_balance

        session.add(entry)

    print(f"✔ Rebuilt user {user_id}")


def rebuild_all_users(session):
    users = session.execute(
        select(CoinsLedgerModel.user_id).distinct()
    ).scalars().all()

    for user_id in users:
        rebuild_user_ledger(user_id, session)

    session.commit()
    print("🎉 All users rebuilt successfully")


if __name__ == "__main__":
    engine = create_engine(VoiceSettings.DB_URL)
    SessionLocal = sessionmaker(bind=engine)

    session = SessionLocal()

    try:
        rebuild_all_users(session)
    except Exception as e:
        session.rollback()
        print(f"❌ Error: {e}")
    finally:
        session.close()