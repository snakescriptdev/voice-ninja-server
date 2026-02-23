from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, ForeignKey, Table, create_engine, Enum, Text, Index, UniqueConstraint
from sqlalchemy.orm import relationship,Mapped,mapped_column
from app_v2.schemas.enum_types import RequestMethodEnum, GenderEnum, PhoneNumberAssignStatus,ChannelEnum,CallStatusEnum, WidgetPosition
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional, List, Dict
from fastapi_sqlalchemy import db
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
import bcrypt
import os
from datetime import datetime
from app_v2.core.config import VoiceSettings
import uuid


# Database configuration
DB_URL = VoiceSettings.DB_URL
engine = create_engine(DB_URL, pool_pre_ping=True)
Base = declarative_base()

class UserModel(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True, nullable=True)
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
                (cls.username == username) | (cls.email == username) | (cls.phone == username)
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
    username = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, nullable=True, index=True)
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
    notification_settings = relationship("UserNotificationSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")
    twilio_user_creds = relationship("TwilioUserCreds", back_populates="user", uselist=False, cascade="all, delete-orphan")
    knowledge_bases = relationship("KnowledgeBaseModel",back_populates="user",cascade="all, delete-orphan")
    functions = relationship("FunctionModel",back_populates="user",cascade="all, delete-orphan")
    conversations = relationship("ConversationsModel",back_populates="user",cascade="all, delete-orphan")
    web_agents = relationship("WebAgentModel", back_populates="user",cascade="all, delete-orphan")
    
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
        """Get user by username, email or phone."""
        with db():
            return db.session.query(cls).filter(
                (cls.username == username) | (cls.email == username) | (cls.phone == username)
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



class TokensToConsume(Base):
    __tablename__ = "tokens_to_consume"
    id = Column(Integer, primary_key=True)
    token_values = Column(Integer, nullable=True, default=0)



class VoiceModel(Base):
    __tablename__ = "custom_voices"
    id = Column(Integer, primary_key=True, index=True)
    voice_name = Column(String, nullable=False)
    is_custom_voice = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    modified_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("unified_auth.id"), nullable=True)
    elevenlabs_voice_id = Column(String, nullable=True)
    has_sample_audio = Column(Boolean,nullable=True)
    audio_file = Column(String, nullable=True)

    user = relationship("UnifiedAuthModel", back_populates="voices")
    agents = relationship("AgentModel",back_populates="voice")
    traits = relationship("VoiceTraitsModel", back_populates="voice", uselist=False, cascade="all, delete-orphan")




class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String,nullable=False,index=True)
    first_message: Mapped[str] = mapped_column(String)
    system_prompt : Mapped[str] = mapped_column(String,nullable=False)

    user_id : Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"))
    agent_voice : Mapped[int] = mapped_column(Integer, ForeignKey("custom_voices.id"))
    elevenlabs_agent_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    built_in_tools: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True, default={})
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True,server_default="true")
    
    user = relationship("UnifiedAuthModel",back_populates="agents")

    voice = relationship("VoiceModel",back_populates="agents")

    agent_ai_models = relationship("AgentAIModelBridge",back_populates="agent",cascade="all, delete-orphan")

    agent_languages = relationship("AgentLanguageBridge",back_populates="agent",cascade="all, delete-orphan")
    agent_functions = relationship("AgentFunctionBridgeModel",back_populates="agent",cascade="all, delete-orphan", order_by="AgentFunctionBridgeModel.id")
    variables = relationship("VariablesModel",back_populates="agent",cascade="all, delete-orphan")
    phone_number = relationship("PhoneNumberService",back_populates="agent")
    agent_knowledge_bases = relationship("AgentKnowledgeBaseBridge",back_populates="agent",cascade="all, delete-orphan", order_by="AgentKnowledgeBaseBridge.id")
    conversations = relationship("ConversationsModel",back_populates="agent",cascade="all, delete-orphan")
    web_agent = relationship("WebAgentModel",back_populates="agent",cascade="all, delete-orphan")



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
    name: Mapped[str] = mapped_column(String,nullable=False)
    description: Mapped[str] = mapped_column(String,nullable=False)
    elevenlabs_tool_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"),nullable=True)

    #audit fields
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


    api_endpoint_url = relationship("FunctionApiConfig",back_populates = "function",cascade= "all, delete-orphan", uselist=False)
    agent_functions = relationship("AgentFunctionBridgeModel",back_populates="function",cascade="all,delete-orphan")
    user = relationship("UnifiedAuthModel",back_populates="functions")


class FunctionApiConfig(Base):
    __tablename__ = "function_api_config"
    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    function_id: Mapped[int] = mapped_column(Integer,ForeignKey("functions.id"))
    endpoint_url: Mapped[str] = mapped_column(String,nullable=False)
    http_method: Mapped[RequestMethodEnum] = mapped_column()
    timeout_ms: Mapped[int] = mapped_column(Integer)
    headers: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    query_params: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    path_params: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    body_schema: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    response_variables: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB))
    speak_while_execution: Mapped[bool] = mapped_column(Boolean,default=False)
    speak_after_execution: Mapped[bool] = mapped_column(Boolean,default=True)

    #audit fields
    created_at: Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    function = relationship("FunctionModel",back_populates="api_endpoint_url")


class AgentFunctionBridgeModel(Base):
    __tablename__ = "agent_function_bridge"
    id : Mapped[int] = mapped_column(Integer,primary_key =True, autoincrement=True,index=True)
    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    function_id: Mapped[int] = mapped_column(Integer,ForeignKey("functions.id"))  

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


class KnowledgeBaseModel(Base):
    __tablename__ = "knowledge_base"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id : Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"))
    kb_type: Mapped[str] = mapped_column(String, nullable=False)  # 'file', 'url', 'text'
    title: Mapped[str] = mapped_column(String, nullable=True) # file name or title
    content_path: Mapped[str] = mapped_column(String, nullable=True) # file path or url
    content_text: Mapped[str] = mapped_column(Text, nullable=True) # for text type
    file_size: Mapped[float] = mapped_column(Float, nullable=True)
    elevenlabs_document_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    rag_index_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("UnifiedAuthModel", back_populates="knowledge_bases")
    agent_knowledge_bases = relationship("AgentKnowledgeBaseBridge",back_populates="knowledge_base",cascade="all, delete-orphan")



class AgentKnowledgeBaseBridge(Base):
    __tablename__ = "agent_knowledgebase_bridge"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)

    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"),nullable=False)
    kb_id: Mapped[int]= mapped_column(Integer,ForeignKey("knowledge_base.id"),nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent = relationship("AgentModel",back_populates="agent_knowledge_bases")
    knowledge_base = relationship("KnowledgeBaseModel",back_populates="agent_knowledge_bases")

    __table_args__ = (
        UniqueConstraint("agent_id","kb_id",name="agent_kb_bridge"),
    )






class UserNotificationSettings(Base):
    __tablename__ = "notification_settings"


    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"),unique=True) #enusre 1:1 

    email_notifications: Mapped[bool] = mapped_column(Boolean,default=True,nullable=False)
    useage_alerts: Mapped[bool] = mapped_column(Boolean,default=True,nullable=False)
    expiry_alert: Mapped[bool] = mapped_column(Boolean,default=True,nullable=False)

    user = relationship("UnifiedAuthModel", back_populates="notification_settings")


class VoiceTraitsModel(Base):
    __tablename__ = "voice_traits"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement= True)

    voice_id: Mapped[int] = mapped_column(Integer, ForeignKey("custom_voices.id"))
    gender: Mapped[GenderEnum] = mapped_column(Enum(GenderEnum),nullable=True)
    nationality: Mapped[str] = mapped_column(String,nullable=True)

    voice = relationship("VoiceModel", back_populates="traits")



class PhoneNumberService(Base):
    __tablename__ = "phone_number_service"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    phone_number: Mapped[str] = mapped_column(String,nullable=False)
    type: Mapped[str] = mapped_column(String,nullable=False)
    user_id: Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"),nullable=False)
    assigned_to: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"),nullable=True,unique=True)
    status: Mapped[PhoneNumberAssignStatus] = mapped_column(Enum(PhoneNumberAssignStatus),default=PhoneNumberAssignStatus.unassigned,nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    modified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    monthly_cost: Mapped[float] = mapped_column(Float,nullable=False)

    #sid
    sid: Mapped[str] = mapped_column(String)

    #relationships
    user = relationship("UnifiedAuthModel", backref="phone_numbers")
    agent = relationship("AgentModel", back_populates="phone_number")

class TwilioUserCreds(Base):
    __tablename__ = "twilio_user_creds"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"),unique=True) #enusre 1:1 

    account_sid: Mapped[str] = mapped_column(String,nullable=False)
    auth_token: Mapped[str] = mapped_column(String,nullable=False)

    user = relationship("UnifiedAuthModel", back_populates="twilio_user_creds")


class ConversationsModel(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True)
    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"),nullable=False,index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("unified_auth.id"),nullable=False,index=True)
    message_count: Mapped[int] = mapped_column(Integer,nullable=True)
    duration: Mapped[int] = mapped_column(Integer,nullable=True)
    call_status: Mapped[CallStatusEnum] = mapped_column(Enum(CallStatusEnum),nullable=True)
    phone_number_id: Mapped[int] = mapped_column(Integer,ForeignKey("phone_number_service.id"),nullable=True)
    channel: Mapped[ChannelEnum] = mapped_column(Enum(ChannelEnum),nullable=True)
    transcript_summary: Mapped[str] = mapped_column(String,nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime,default= datetime.utcnow)
    elevenlabs_conv_id: Mapped[str] = mapped_column(String,nullable=True)
    cost: Mapped[int] = mapped_column(Integer,nullable=True)
    #relationships
    agent = relationship("AgentModel",back_populates="conversations")
    user = relationship("UnifiedAuthModel",back_populates="conversations")
    # web_agent = relationship("WebAgentLeadModel",back_populates="conversations",cascade="all, delete-orphan")

class WebAgentModel(Base):
    __tablename__ = "web_agents"

    id: Mapped[int] = mapped_column(primary_key=True)

    public_id: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4())
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("unified_auth.id"),
        nullable=False
    )

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id"),
        nullable=False
    )

    web_agent_name: Mapped[str] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(Boolean,default=True)

    # Appearance
    widget_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    widget_subtitle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_color: Mapped[str] = mapped_column(String(20), default="#562C7C")

    position: Mapped[str] = mapped_column(
        Enum("top-right", "top-left", "bottom-right", "bottom-left", name="widget_position"),
        default="bottom-right"
    )

    show_branding: Mapped[bool] = mapped_column(Boolean, default=True)

    # Prechat
    enable_prechat: Mapped[bool] = mapped_column(Boolean, default=False)
    require_name: Mapped[bool] = mapped_column(Boolean, default=False)
    require_email: Mapped[bool] = mapped_column(Boolean, default=False)
    require_phone: Mapped[bool] = mapped_column(Boolean, default=False)

    custom_fields: Mapped[list | None] = mapped_column(MutableList.as_mutable(JSONB), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("UnifiedAuthModel", back_populates="web_agents")
    agent = relationship("AgentModel",back_populates="web_agent")
    leads = relationship("WebAgentLeadModel", back_populates="web_agent")


class WebAgentLeadModel(Base):
    __tablename__ = "web_agent_leads"

    id: Mapped[int] = mapped_column(primary_key=True)

    web_agent_id: Mapped[int] = mapped_column(
        ForeignKey("web_agents.id"),
        nullable=False
    )

    # conversation_id: Mapped[int] = mapped_column(Integer,ForeignKey("conversations.id"),nullable=True)

    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))

    custom_data: Mapped[list | None] = mapped_column(MutableList.as_mutable(JSONB))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    web_agent = relationship("WebAgentModel", back_populates="leads")
    # conversations = relationship("ConversationsModel",back_populates="web_agent")

class ActivityLogModel(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False, index=True)
    
    event_type: Mapped[str] = mapped_column(String(100), index=True) # e.g., agent_created, call_made
    description: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True) # Renamed to avoid reserved word confusion if any
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("UnifiedAuthModel")