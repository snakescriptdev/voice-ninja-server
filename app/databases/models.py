from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional, List
from fastapi_sqlalchemy import db

Base = declarative_base()

class AudioRecordModel(Base):
    __tablename__ = "audio_records"
    
    id = Column(Integer, primary_key=True)
    file_path = Column(String, nullable=False)  # Store the full path to audio file
    file_name = Column(String, nullable=False)  # Store the encoded filename
    duration = Column(Float, nullable=True)     # Duration in seconds
    voice = Column(String, nullable=True)       # Voice type/model used
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def get_file_url(self, request) -> str:
        """
        Get the complete URL for the audio file
        Args:
            request: FastAPI request object
        Returns:
            str: Complete URL to access the audio file
        """
        if request:
            return f"{request.base_url._url}audio/{self.file_name}/"
        return f"/audio/{self.file_name}"

    def __repr__(self):
        return f"<AudioRecord(id={self.id}, file_name={self.file_name})>"

    @classmethod
    def create_record(cls, file_path: str, file_name: str, voice: str, duration: float) -> "AudioRecordModel":
        """
        Create a new audio record
        """
        with db():
            record = cls(
                file_path=file_path,
                file_name=file_name,
                voice=voice,
                duration=duration
            )
            db.session.add(record)
            db.session.commit()
            db.session.refresh(record)
            return record

    @classmethod
    def get_by_id(cls, record_id: int) -> Optional["AudioRecordModel"]:
        """
        Get audio record by ID
        """
        with db():
            return db.session.query(cls).filter(cls.id == record_id).first()

    @classmethod
    def get_by_voice(cls, voice: str) -> List["AudioRecordModel"]:
        """
        Get all audio records for a specific voice
        """
        with db():
            return db.session.query(cls).filter(cls.voice == voice).all()

    @classmethod
    def get_recent_records(cls, limit: int = 10) -> List["AudioRecordModel"]:
        """
        Get most recent audio records
        """
        with db():
            return db.session.query(cls).order_by(cls.created_at.desc()).limit(limit).all()

    def update(self, **kwargs) -> "AudioRecordModel":
        """
        Update audio record fields
        """
        with db():
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            db.session.add(self)
            db.session.commit()
            db.session.refresh(self)
            return self

    def delete(self) -> bool:
        """
        Delete audio record
        """
        try:
            with db():
                db.session.delete(self)
                db.session.commit()
            return True
        except Exception:
            return False
