from sqlalchemy import Integer, ForeignKey,UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app_v2.databases.base import Base, TimeStampMixin



class AgentAIModelBridge(Base,TimeStampMixin):

    __tablename__ = "agent_ai_model_bridge"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True,index=True)
    agent_id : Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    ai_model_id: Mapped[int] = mapped_column(Integer,ForeignKey("ai_models.id"))

    agent = relationship("AgentModel",back_populates="agent_ai_models")
    ai_model = relationship("AIModels",back_populates="agent_ai_models")

    __table_args__ = (
        UniqueConstraint("agent_id","ai_model_id",name="uq_agebt_ai_model_bridge_agent_id_ai_model"),
        Index("ix_agent_ai_model_agent_id","agent_id"),
        Index("ix_agent_ai_model_ai_model_id","ai_model_id")

    )

