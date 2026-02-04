from sqlalchemy.orm import Session,sessionmaker
from app_v2.databases.models import AdminTokenModel, TokensToConsume, VoiceModel, Base, engine, VoiceTraitsModel
from app_v2.core.config import VoiceSettings
sessionLocal = sessionmaker(bind=engine,autoflush=False,autocommit= False)



def populate_default_data():
    """
    Populates the database with default data if it doesn't exist.
    Also ensures all tables are created.
    """
    # Ensure tables exist
    Base.metadata.create_all(engine)
    session = sessionLocal()

    

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
        existing = (
            session.query(VoiceModel)
            .filter(
                VoiceModel.voice_name == name,
                VoiceModel.is_custom_voice == False
            )
            .first()
        )

        # Case 1: Voice does not exist → create voice + traits
        if not existing:
            print(f"Creating default voice: {name}")
            voice = VoiceModel(voice_name=name, is_custom_voice=False)
            session.add(voice)
            session.flush()  # 🔑 ensures voice.id is available

            print(f"Creating default traits for {name}")
            traits = VoiceTraitsModel(voice_id=voice.id)
            session.add(traits)
            continue

        # Case 2: Voice exists → check traits
        existing_traits = (
            session.query(VoiceTraitsModel)
            .filter(VoiceTraitsModel.voice_id == existing.id)
            .first()
        )

        if not existing_traits:
            print(f"Creating default traits for {name}")
            traits = VoiceTraitsModel(voice_id=existing.id)
            session.add(traits)

            
    try:
        session.commit()
        print("Default data population complete.")
    except Exception as e:
        session.rollback()
        print(f"Error populating data: {e}")
        raise e


populate_default_data()