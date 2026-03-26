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


from collections import deque
from sqlalchemy import select

def rebuild_user_ledger(user_id, session, allow_negative=True, flush_batch=500):
    """
    Rebuilds ledger with:
    - Correct FIFO consumption
    - Proper negative balance handling (deficit recovery)
    - Safe handling of corrupted/null data
    """

    entries = session.execute(
        select(CoinsLedgerModel)
        .where(CoinsLedgerModel.user_id == user_id)
        .order_by(
            CoinsLedgerModel.created_at.asc(),
            CoinsLedgerModel.id.asc()
        )
    ).scalars().all()

    running_balance = 0
    fifo_queue = deque()
    processed = 0

    for entry in entries:
        coins = entry.coins or 0

        # --- NORMALIZE BAD DATA ---
        if coins == 0:
            entry.remaining_coins = 0

        # =========================
        # ➕ CREDIT
        # =========================
        elif coins > 0:
            remaining = coins

            # --- DEFICIT RECOVERY ---
            if running_balance < 0:
                adjust = min(remaining, abs(running_balance))
                running_balance += adjust
                remaining -= adjust

            # Only push to FIFO if something is actually usable
            if remaining > 0:
                entry.remaining_coins = remaining
                fifo_queue.append(entry)
            else:
                entry.remaining_coins = 0

        # =========================
        # ➖ DEBIT
        # =========================
        elif coins < 0:
            to_deduct = abs(coins)

            # --- OPTIONAL HARD STOP ---
            if not allow_negative:
                total_available = sum(
                    (e.remaining_coins or 0) for e in fifo_queue
                )
                if to_deduct > total_available:
                    raise Exception(
                        f"Insufficient balance for user {user_id}"
                    )

            # --- FIFO DEDUCTION ---
            while to_deduct > 0 and fifo_queue:
                credit_entry = fifo_queue[0]
                available = credit_entry.remaining_coins or 0

                if available <= 0:
                    fifo_queue.popleft()
                    continue

                if available <= to_deduct:
                    to_deduct -= available
                    credit_entry.remaining_coins = 0
                    fifo_queue.popleft()
                else:
                    credit_entry.remaining_coins -= to_deduct
                    to_deduct = 0

            # If still left → deficit (negative balance)
            entry.remaining_coins = 0

            if to_deduct > 0:
                running_balance -= to_deduct  # track deficit

        # =========================
        # 📊 UPDATE BALANCE
        # =========================
        running_balance += coins
        entry.balance_after = running_balance

        session.add(entry)
        processed += 1

        # --- BATCH FLUSH (PERFORMANCE) ---
        if processed % flush_batch == 0:
            session.flush()

    session.flush()

    print(f"✔ Rebuilt user {user_id} | Final balance: {running_balance}")


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