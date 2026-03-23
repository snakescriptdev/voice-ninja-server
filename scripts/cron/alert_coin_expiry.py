import sys
import os
import asyncio
from datetime import datetime
from sqlalchemy import text
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env')))

def get_base_url():
    # Return FRONTEND_URL if set, else a fallback
    return os.getenv("FRONTEND_URL")

async def run_expiry_alert():
    """
    Cron job script to send warning emails to users whose coins are expiring
    exactly between 2 to 3 days from now.
    """
    print(f"[{datetime.utcnow()}] Starting coin expiry alert process...")
    
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app_v2.core.config import VoiceSettings
    from app_v2.utils.email_service import send_coin_expiry_alert_email
    
    engine = create_engine(VoiceSettings.DB_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Find users with coins expiring in the next 2-3 days
        # We target this window to avoid sending multiple alerts for the same cohort.
        result = session.execute(text("""
            SELECT 
                ua.email, 
                COALESCE(ua.name, ua.first_name || ' ' || ua.last_name, ua.username) as user_name,
                SUM(cl.remaining_coins) as expiring_coins,
                cl.expiry_at as expiry_date_raw
            FROM coins_ledger cl
            JOIN unified_auth ua ON cl.user_id = ua.id
            WHERE cl.remaining_coins > 0
              AND cl.expiry_at IS NOT NULL
              -- Between 2 and 3 days from now
              AND cl.expiry_at > CURRENT_TIMESTAMP + INTERVAL '2 days'
              AND cl.expiry_at <= CURRENT_TIMESTAMP + INTERVAL '3 days'
              AND ua.email IS NOT NULL 
              AND LENGTH(TRIM(ua.email)) > 0
            GROUP BY ua.id, ua.email, ua.name, ua.first_name, ua.last_name, ua.username, cl.expiry_at
        """)).mappings().all()

        alert_count = 0
        base_url = get_base_url()

        for row in result:
            user_email = row['email']
            user_name = row['user_name']
            expiring_coins = row['expiring_coins']
            expiry_date_raw = row['expiry_date_raw']
            
            # Format the date safely in python
            if isinstance(expiry_date_raw, str):
                expiry_date = expiry_date_raw
            else:
                expiry_date = expiry_date_raw.strftime('%Y-%m-%d %H:%M:%S') if expiry_date_raw else 'Unknown'

            print(f"[{datetime.utcnow()}] Alerting {user_email} about {expiring_coins} coins expiring on {expiry_date}")
            
            await send_coin_expiry_alert_email(
                user_email=user_email,
                expiring_coins=int(expiring_coins),
                expiry_date=expiry_date,
                base_url=base_url,
                user_name=user_name
            )
            alert_count += 1

        print(f"[{datetime.utcnow()}] Alerted {alert_count} users successfully.")
        
    except Exception as e:
        print(f"Error during coin expiry alert process: {e}")
        sys.exit(1)
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(run_expiry_alert())
