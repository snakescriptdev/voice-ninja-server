from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, Boolean, ForeignKey
from app_v2.databases.base import Base, TimeStampMixin



class AgentModel(Base,TimeStampMixin):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String,nullable=False,index=True)
    first_message: Mapped[str] = mapped_column(String)
    system_prompt : Mapped[str] = mapped_column(String,nullable=False)

    user_id : Mapped[int] = mapped_column(Integer,ForeignKey("users.id"))
    agent_voice : Mapped[int] = mapped_column(Integer, ForeignKey("custom_voices.id"))

    user = relationship("UserModel",back_populates="agents")

    voice = relationship("VoiceModel",back_populates="agents")

    agent_ai_models = relationship("AgentAIModelBridge",back_populates="agent",cascade="all, delete-orphan")

    agent_languages = relationship("AgentLanguageBridge",back_populates="agent",cascade="all, delete-orphan")
