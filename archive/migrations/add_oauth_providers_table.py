"""Database migration to add oauth_providers table.

This script creates the oauth_providers table for tracking OAuth authentication.
"""

import os
import sys
from sqlalchemy import create_engine, text

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

DB_URL = os.getenv("DB_URL", "postgresql://postgres:1234@localhost/voice_ninja")

def run_migration():
    """Create oauth_providers table."""
    engine = create_engine(DB_URL)
    
    with engine.connect() as conn:
        # Create oauth_providers table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS oauth_providers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider VARCHAR NOT NULL,
                provider_user_id VARCHAR NOT NULL,
                email VARCHAR NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, provider_user_id)
            );
        """))
        
        # Create index for faster lookups
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_oauth_providers_user_id 
            ON oauth_providers(user_id);
        """))
        
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_oauth_providers_email 
            ON oauth_providers(email);
        """))
        
        conn.commit()
        print("✅ Successfully created oauth_providers table")

if __name__ == "__main__":
    try:
        run_migration()
        print("Migration completed successfully!")
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        sys.exit(1)
