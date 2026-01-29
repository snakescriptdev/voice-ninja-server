from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from app_v2.databases.base import Base


class VoiceModel(Base):
    __tablename__ = "custom_voices"

    id = Column(Integer, primary_key=True, index=True)
    voice_name = Column(String, nullable=False)
    is_custom_voice = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    modified_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    elevenlabs_voice_id = Column(String, nullable=True)
    audio_file = Column(String, nullable=True)

    user = relationship("UserModel", back_populates="voices")   

    agents = relationship("AgentModel",back_populates="voice")

    # ---------- BOOTSTRAP ----------

    @classmethod
    def ensure_default_voices(
        cls,
        session: Session
    ) -> None:
        allowed_voices = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]

        for name in allowed_voices:
            exists = (
                session
                .query(cls)
                .filter(
                    cls.voice_name == name,
                    cls.is_custom_voice.is_(False)
                )
                .first()
            )

            if not exists:
                session.add(
                    cls(
                        voice_name=name,
                        is_custom_voice=False
                    )
                )

        session.commit()

    # ---------- ASSIGN VOICE TO AGENT ----------

    @classmethod
    def set_for_agent(
        cls,
        session: Session,
        agent_id: int,
        voice_id: int
    ) -> "VoiceModel":
        """
        Assign an existing voice to an agent
        """

        voice = (
            session
            .query(cls)
            .filter(cls.id == voice_id)
            .first()
        )

        if not voice:
            raise ValueError("Voice not found")

        voice.agent_id = agent_id

        session.commit()
        session.refresh(voice)

        return voice
