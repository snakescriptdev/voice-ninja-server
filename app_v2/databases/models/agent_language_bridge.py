from sqlalchemy.orm import Mapped, mapped_column, relationship

from app_v2.databases.base import Base, TimeStampMixin
from sqlalchemy import  Integer, ForeignKey, Index,UniqueConstraint


class AgentLanguageBridge(Base,TimeStampMixin):

    __tablename__ = "agent_language_bridge"


    id: Mapped[int] = mapped_column(Integer, primary_key= True, index= True,autoincrement=True)

    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    lang_id: Mapped[int]  = mapped_column(Integer,ForeignKey("languages.id"))

    __table_args__ = (
                    UniqueConstraint("agent_id","lang_id",name="uq_lang_bridge_agent_id_lang_id"),
                    Index("ix_agent_lang_bridge_agent_id","agent_id"),
                    Index("ix_agent_llang_bridge_lang_id","lang_id")
        
    )

    agent = relationship("AgentModel",back_populates="agent_languages")
    language = relationship("LanguageModel",back_populates="agent_languages")


