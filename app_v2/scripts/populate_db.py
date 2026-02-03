from sqlalchemy.orm import Session
from app_v2.databases.models import AdminTokenModel, TokensToConsume, VoiceModel
from fastapi_sqlalchemy import db

def populate_default_data(session: Session = None):
    """
    Populates the database with default data if it doesn't exist.
    This replaces the individual ensure_default_exists methods in models.
    """
    # If session is provided use it, otherwise use db.session context
    # Note: caller might be responsible for the session context
    
    local_session = False
    if session is None:
        # In case we are running this outside of a request context where db.session is available
        # we might need to handle session creation. 
        # However, looking at how it was used in main.py, it likely relies on the middleware or global db context.
        # For now, let's assume we are called within a context where `db.session` works OR a session is passed.
        # But `db.session` from `fastapi_sqlalchemy` usually requires a context manager if not in a request.
        
        # Checking how it was implemented in models.py:
        # with db(): ...
        try:
           session = db.session
        except:
            # Fallback if no session is active (e.g. CLI script)
            # We assume the caller handles the `with db():` block or passes a session
            pass

    print("Checking default data...")

    # Admin Tokens
    default_token = session.query(AdminTokenModel).filter(AdminTokenModel.id == 1).first()
    if not default_token:
        print("Creating default AdminTokenModel...")
        default_token = AdminTokenModel(id=1, token_values=0, free_tokens=0)
        session.add(default_token)
    
    # Tokens To Consume
    default_consume = session.query(TokensToConsume).filter(TokensToConsume.id == 1).first()
    if not default_consume:
        print("Creating default TokensToConsume...")
        default_consume = TokensToConsume(id=1, token_values=0)
        session.add(default_consume)
    
    # Default Voices
    allowed_voices = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]
    for name in allowed_voices:
        existing = session.query(VoiceModel).filter(VoiceModel.voice_name == name, VoiceModel.is_custom_voice == False).first()
        if not existing:
            print(f"Creating default voice: {name}")
            voice = VoiceModel(voice_name=name, is_custom_voice=False)
            session.add(voice)
            
    try:
        session.commit()
        print("Default data population complete.")
    except Exception as e:
        session.rollback()
        print(f"Error populating data: {e}")
        raise e
