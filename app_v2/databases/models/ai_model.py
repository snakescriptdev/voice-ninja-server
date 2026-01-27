from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app_v2.databases.base import Base, TimeStampMixin



class AIModels(Base,TimeStampMixin):

    __tablename__= "ai_models"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)
    provider: Mapped[str] = mapped_column(String,nullable=False)
    model_name: Mapped[str] = mapped_column(String,nullable=False,unique=True)

    agent_ai_models =  relationship("AgentAIModelBridge",back_populates="ai_model",cascade="all, delete-orphan")
