import sys
import os
from datetime import datetime
from sqlalchemy import text
from dotenv import load_dotenv
import os 

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env')))

# from main import app 

def run_expiry():
    """
    Cron job script to expire coins using raw SQL via SQLAlchemy.
    """
    print(f"[{datetime.utcnow()}] Starting coin expiry process...")
    
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app_v2.core.config import VoiceSettings
    
    engine = create_engine(VoiceSettings.DB_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Part 1: EXPIRE COINS BY EXPIRY DATE
        
        # 1A. Calculate total expired coins for each user
        session.execute(text("""
            CREATE TEMP TABLE user_expired_totals AS
            SELECT 
                user_id, 
                SUM(remaining_coins) as total_deduction
            FROM coins_ledger
            WHERE remaining_coins > 0
              AND expiry_at IS NOT NULL
              AND expiry_at <= CURRENT_TIMESTAMP
            GROUP BY user_id;
        """))

        # 1B. Zero out remaining coins for expired batches
        result = session.execute(text("""
            UPDATE coins_ledger
            SET remaining_coins = 0
            WHERE remaining_coins > 0
              AND expiry_at IS NOT NULL
              AND expiry_at <= CURRENT_TIMESTAMP;
        """))
        print(f"[{datetime.utcnow()}] Zeroed out {result.rowcount} expired batches.")

        # 1C. Insert 'expired' ledger entries for affected users
        session.execute(text("""
            INSERT INTO coins_ledger (
                user_id, 
                transaction_type, 
                coins, 
                reference_type, 
                balance_after, 
                remaining_coins, 
                created_at
            )
            SELECT 
                uet.user_id,
                'expired',
                -uet.total_deduction,
                'expiry',
                (SELECT COALESCE(SUM(remaining_coins), 0) FROM coins_ledger cl WHERE cl.user_id = uet.user_id),
                0,
                CURRENT_TIMESTAMP
            FROM user_expired_totals uet;
        """))

        session.execute(text("DROP TABLE user_expired_totals;"))
        
        session.commit()
        print(f"[{datetime.utcnow()}] Coin expiry process completed successfully.")
        
    except Exception as e:
        session.rollback()
        print(f"Error during coin expiry: {e}")
        sys.exit(1)
    finally:
        session.close()

if __name__ == "__main__":
    run_expiry()
