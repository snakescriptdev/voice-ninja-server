from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, ForeignKey, Table, create_engine, Enum, Text, Index, UniqueConstraint
from sqlalchemy.orm import relationship,Mapped,mapped_column
from app_v2.schemas.enum_types import RequestMethodEnum
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional, List, Dict
from fastapi_sqlalchemy import db
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
import bcrypt
import os
from datetime import datetime
from app_v2.core.config import VoiceSettings

# Database configuration
DB_URL = VoiceSettings.DB_URL
engine = create_engine(DB_URL, pool_pre_ping=True)
Base = declarative_base()

class UserModel(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=True, default="")
    phone = Column(String, nullable=True, default="")
    password = Column(String, nullable=True, default="")
    name = Column(String, nullable=True, default="")
    first_name = Column(String, nullable=True, default="")
    last_name = Column(String, nullable=True, default="")
    address = Column(String, nullable=True, default="")
    is_verified = Column(Boolean, nullable=True, default=False)
    otp_code = Column(String, nullable=True, default="")
    otp_expires_at = Column(DateTime, nullable=True)
    last_login = Column(DateTime, nullable=True, default=func.now())
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    tokens = Column(Integer, nullable=True, default=0)
    is_admin = Column(Boolean, default=False)
    
    
    
    @classmethod
    def get_by_id(cls, user_id: int) -> Optional["UserModel"]:
        with db():
            return db.session.query(cls).filter(cls.id == user_id).first()

    @classmethod
    def get_by_email(cls, email: str) -> Optional["UserModel"]:
        with db():
            return db.session.query(cls).filter(cls.email == email).first()
    
    @classmethod
    def get_by_username(cls, username: str) -> Optional["UserModel"]:
        with db():
            return db.session.query(cls).filter(
                (cls.email == username) | (cls.phone == username)
            ).first()

    @classmethod
    def update(cls, user_id: int, **kwargs) -> Optional["UserModel"]:
        with db():
            user = db.session.query(cls).filter(cls.id == user_id).first()
            if user:
                for key, value in kwargs.items():
                    if hasattr(user, key):
                        setattr(user, key, value)
                db.session.commit()
                db.session.refresh(user)
                return user
            return None

class OAuthProviderModel(Base):
    __tablename__ = "oauth_providers"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String, nullable=False)
    provider_user_id = Column(String, nullable=False)
    email = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    user = relationship("UserModel", backref="oauth_providers")
    
    @classmethod
    def get_by_provider_and_user_id(cls, provider: str, provider_user_id: str) -> Optional["OAuthProviderModel"]:
        with db():
            return db.session.query(cls).filter(
                cls.provider == provider,
                cls.provider_user_id == provider_user_id
            ).first()

    @classmethod
    def get_by_provider_and_email(cls, provider: str, email: str) -> Optional["OAuthProviderModel"]:
        with db():
            return db.session.query(cls).filter(
                cls.provider == provider,
                cls.email == email
            ).first()
    
    @classmethod
    def create(cls, user_id: int, provider: str, provider_user_id: str, email: str) -> "OAuthProviderModel":
        with db():
            oauth_provider = cls(user_id=user_id, provider=provider, provider_user_id=provider_user_id, email=email)
            db.session.add(oauth_provider)
            db.session.commit()
            db.session.refresh(oauth_provider)
            return oauth_provider

class UnifiedAuthModel(Base):
    """Unified authentication model that tracks all user authentication methods.
    
    This model allows users to sign in with either OTP or Google OAuth,
    regardless of which method they used to initially sign up.
    """
    __tablename__ = "unified_auth"
    
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    phone = Column(String, nullable=True, default="")
    name = Column(String, nullable=True, default="")
    first_name = Column(String, nullable=True, default="")
    last_name = Column(String, nullable=True, default="")
    address = Column(String, nullable=True, default="")
    is_verified = Column(Boolean, default=False)
    tokens = Column(Integer, default=0)
    is_admin = Column(Boolean, default=False)
    
    # OTP authentication fields
    has_otp_auth = Column(Boolean, default=False)
    otp_code = Column(String, nullable=True, default="")
    otp_expires_at = Column(DateTime, nullable=True)
    
    # Google OAuth fields
    has_google_auth = Column(Boolean, default=False)
    google_user_id = Column(String, nullable=True, default="")
    
    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    agents = relationship("AgentModel", back_populates="user")
    voices = relationship("VoiceModel", back_populates="user")

    
    @classmethod
    def get_by_id(cls, user_id: int) -> Optional["UnifiedAuthModel"]:
        with db():
            return db.session.query(cls).filter(cls.id == user_id).first()
    
    @classmethod
    def get_by_email(cls, email: str) -> Optional["UnifiedAuthModel"]:
        with db():
            return db.session.query(cls).filter(cls.email == email).first()
    
    @classmethod
    def get_by_phone(cls, phone: str) -> Optional["UnifiedAuthModel"]:
        with db():
            return db.session.query(cls).filter(cls.phone == phone).first()
    
    @classmethod
    def get_by_username(cls, username: str) -> Optional["UnifiedAuthModel"]:
        """Get user by email or phone."""
        with db():
            return db.session.query(cls).filter(
                (cls.email == username) | (cls.phone == username)
            ).first()
    
    @classmethod
    def get_by_google_id(cls, google_user_id: str) -> Optional["UnifiedAuthModel"]:
        with db():
            return db.session.query(cls).filter(cls.google_user_id == google_user_id).first()
    
    @classmethod
    def create(cls, **kwargs) -> "UnifiedAuthModel":
        with db():
            user = cls(**kwargs)
            db.session.add(user)
            db.session.commit()
            db.session.refresh(user)
            return user
    
    @classmethod
    def update(cls, user_id: int, **kwargs) -> Optional["UnifiedAuthModel"]:
        with db():
            user = db.session.query(cls).filter(cls.id == user_id).first()
            if user:
                for key, value in kwargs.items():
                    if hasattr(user, key):
                        setattr(user, key, value)
                db.session.commit()
                db.session.refresh(user)
                return user
            return None

class AdminTokenModel(Base):
    __tablename__ = "admin_tokens"
    id = Column(Integer, primary_key=True)
    token_values = Column(Integer, nullable=True, default=0)
    free_tokens = Column(Integer, nullable=True, default=0)

    @classmethod
    def ensure_default_exists(cls) -> "AdminTokenModel":
        with db():
            default_token = db.session.query(cls).filter(cls.id == 1).first()
            if not default_token:
                default_token = cls(id=1, token_values=0, free_tokens=0)
                db.session.add(default_token)
                db.session.commit()
            return default_token

class TokensToConsume(Base):
    __tablename__ = "tokens_to_consume"
    id = Column(Integer, primary_key=True)
    token_values = Column(Integer, nullable=True, default=0)

    @classmethod
    def ensure_default_exists(cls) -> "TokensToConsume":
        with db():
            default_token = db.session.query(cls).filter(cls.id == 1).first()
            if not default_token:
                default_token = cls(id=1, token_values=0)
                db.session.add(default_token)
                db.session.commit()
            return default_token

class VoiceModel(Base):
    __tablename__ = "custom_voices"
    id = Column(Integer, primary_key=True, index=True)
    voice_name = Column(String, nullable=False)
    is_custom_voice = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    modified_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("unified_auth.id"), nullable=True)
    elevenlabs_voice_id = Column(String, nullable=True)
    audio_file = Column(String, nullable=True)

    user = relationship("UnifiedAuthModel", back_populates="voices")
    agents = relationship("AgentModel",back_populates="voice")

    @classmethod
    def ensure_default_voices(cls):
        from app_v2.core.config import VoiceSettings
        allowed_voices = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"] # Default fallback
        with db():
            for name in allowed_voices:
                existing = db.session.query(cls).filter(cls.voice_name == name, cls.is_custom_voice == False).first()
                if not existing:
                    voice = cls(voice_name=name, is_custom_voice=False)
                    db.session.add(voice)
            db.session.commit()


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String,nullable=False,index=True)
    first_message: Mapped[str] = mapped_column(String)
    system_prompt : Mapped[str] = mapped_column(String,nullable=False)

    user_id : Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"))
    agent_voice : Mapped[int] = mapped_column(Integer, ForeignKey("custom_voices.id"))
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    phone : Mapped[str] = mapped_column(String,default="not assigned",nullable=True)

    user = relationship("UnifiedAuthModel",back_populates="agents")

    voice = relationship("VoiceModel",back_populates="agents")

    agent_ai_models = relationship("AgentAIModelBridge",back_populates="agent",cascade="all, delete-orphan")

    agent_languages = relationship("AgentLanguageBridge",back_populates="agent",cascade="all, delete-orphan")
    agent_functions = relationship("AgentFunctionBridgeModel",back_populates="agent",cascade="all, delete-orphan")
    variables = relationship("VariablesModel",back_populates="agent",cascade="all, delete-orphan")



class AIModels(Base):

    __tablename__= "ai_models"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)
    provider: Mapped[str] = mapped_column(String,nullable=False)
    model_name: Mapped[str] = mapped_column(String,nullable=False,unique=True)
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent_ai_models =  relationship("AgentAIModelBridge",back_populates="ai_model",cascade="all, delete-orphan")

class LanguageModel(Base):

    __tablename__ = "languages"

    id: Mapped[int] = mapped_column(Integer,autoincrement=True,index=True,primary_key=True)
    lang_code: Mapped[str] = mapped_column(String, nullable=False,unique=True)
    language: Mapped[str] = mapped_column(String,nullable=False,unique=True)
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent_languages = relationship("AgentLanguageBridge",back_populates="language",cascade="all, delete-orphan")


class AgentAIModelBridge(Base):

    __tablename__ = "agent_ai_model_bridge"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True,index=True)
    agent_id : Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    ai_model_id: Mapped[int] = mapped_column(Integer,ForeignKey("ai_models.id"))
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent = relationship("AgentModel",back_populates="agent_ai_models")
    ai_model = relationship("AIModels",back_populates="agent_ai_models")

    __table_args__ = (
        UniqueConstraint("agent_id","ai_model_id",name="uq_agebt_ai_model_bridge_agent_id_ai_model"),
        Index("ix_agent_ai_model_agent_id","agent_id"),
        Index("ix_agent_ai_model_ai_model_id","ai_model_id")

    )

class AgentLanguageBridge(Base):

    __tablename__ = "agent_language_bridge"


    id: Mapped[int] = mapped_column(Integer, primary_key= True, index= True,autoincrement=True)

    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    lang_id: Mapped[int]  = mapped_column(Integer,ForeignKey("languages.id"))
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
                    UniqueConstraint("agent_id","lang_id",name="uq_lang_bridge_agent_id_lang_id"),
                    Index("ix_agent_lang_bridge_agent_id","agent_id"),
                    Index("ix_agent_llang_bridge_lang_id","lang_id")
        
    )

    agent = relationship("AgentModel",back_populates="agent_languages")
    language = relationship("LanguageModel",back_populates="agent_languages")



class FunctionModel(Base):
    __tablename__ = "functions"
    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)
    name: Mapped[str] = mapped_column(String,unique=True,nullable=False)
    description: Mapped[str] = mapped_column(String,nullable=False)

    #audit fields
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


    api_endpoint_url = relationship("FunctionApiConfig",back_populates = "function",cascade= "all, delete-orphan")
    agent_functions = relationship("AgentFunctionBridgeModel",back_populates="function",cascade="all,delete-orphan")



class FunctionApiConfig(Base):
    __tablename__ = "function_api_config"
    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    function_id: Mapped[int] = mapped_column(Integer,ForeignKey("functions.id"))
    endpoint_url: Mapped[str] = mapped_column(String,nullable=False)
    http_method: Mapped[RequestMethodEnum] = mapped_column()
    timeout_ms: Mapped[int] = mapped_column(Integer)
    headers: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    query_params: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    llm_response_schema: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    response_variables: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))

    #audit fields
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    function = relationship("FunctionModel",back_populates="api_endpoint_url")


class AgentFunctionBridgeModel(Base):
    __tablename__ = "agent_function_bridge"
    id : Mapped[int] = mapped_column(Integer,primary_key =True, autoincrement=True,index=True)
    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    function_id: Mapped[int] = mapped_column(Integer,ForeignKey("functions.id"))
    speak_while_execution: Mapped[bool] = mapped_column(Boolean,default=False)
    speak_after_execution: Mapped[bool] = mapped_column(Boolean,default=True)

    #audit fields
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    #relationships
    agent = relationship("AgentModel",back_populates="agent_functions")
    function = relationship("FunctionModel",back_populates="agent_functions")





class VariablesModel(Base):

    __tablename__ = "variables"
    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    variable_name: Mapped[str]= mapped_column(String,nullable=False)
    variable_value: Mapped[str] = mapped_column(String,nullable=False)
    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent = relationship("AgentModel",back_populates="variables")






Base.metadata.create_all(engine)
