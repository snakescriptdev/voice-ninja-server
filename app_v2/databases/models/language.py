from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, String
from app_v2.databases.base import Base, TimeStampMixin


class LanguageModel(Base,TimeStampMixin):

    __tablename__ = "languages"

    id: Mapped[int] = mapped_column(Integer,autoincrement=True,index=True,primary_key=True)
    lang_code: Mapped[str] = mapped_column(String, nullable=False,unique=True)
    language: Mapped[str] = mapped_column(String,nullable=False,unique=True)

    agent_languages = relationship("AgentLanguageBridge",back_populates="language",cascade="all, delete-orphan")


