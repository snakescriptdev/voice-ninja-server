from app_v2.databases.base import Base
from app_v2.core.config import VoiceSettings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker



engine = create_engine(url=VoiceSettings.DB_URL)


SessionLocal = sessionmaker(bind=engine)