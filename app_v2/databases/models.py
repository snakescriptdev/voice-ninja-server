from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, ForeignKey, Table, create_engine, Enum, Text, Index, UniqueConstraint
from sqlalchemy.orm import relationship,Mapped,mapped_column
from app_v2.schemas.enum_types import RequestMethodEnum, GenderEnum, PhoneNumberAssignStatus,ChannelEnum,CallStatusEnum, WidgetPosition, PaymentProviderEnum, PaymentStatusEnum, PaymentTypeEnum, CoinTransactionTypeEnum, PublicLogChannelEnum, SupportTicketCategoryEnum, SupportTicketStatusEnum
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional, List, Dict
from fastapi_sqlalchemy import db
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
import bcrypt
import os
from datetime import datetime, timezone, timezone
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
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True, default=func.now())
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
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
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
    
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
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    
    # Google OAuth fields
    has_google_auth = Column(Boolean, default=False)
    google_user_id = Column(String, nullable=True, default="")
    is_suspended = Column(Boolean, default=False,server_default="false")
    suspension_reason = Column(String, nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    # Persisted dismissal for the two low-credit header banners, so a user's
    # "x" click sticks across devices/sessions. `_recovered` tracks whether the
    # balance has gone back above the banner's threshold at least once since it
    # was dismissed — once it drops below the threshold again after that, the
    # dismissal is cleared (re-armed) so the warning resurfaces for the new
    # low-balance episode instead of staying hidden forever.
    low_credits_banner_dismissed = Column(Boolean, default=False, server_default="false")
    low_credits_banner_recovered = Column(Boolean, default=False, server_default="false")
    critical_credits_banner_dismissed = Column(Boolean, default=False, server_default="false")
    critical_credits_banner_recovered = Column(Boolean, default=False, server_default="false")

    agents = relationship("AgentModel", back_populates="user")
    voices = relationship("VoiceModel", back_populates="user")
    notification_settings = relationship("UserNotificationSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")
    twilio_user_creds = relationship("TwilioUserCreds", back_populates="user", cascade="all, delete-orphan")
    knowledge_bases = relationship("KnowledgeBaseModel",back_populates="user",cascade="all, delete-orphan")
    functions = relationship("FunctionModel",back_populates="user",cascade="all, delete-orphan")
    conversations = relationship("ConversationsModel",back_populates="user",cascade="all, delete-orphan")
    widgets = relationship("WidgetModel", back_populates="user",cascade="all, delete-orphan")
    web_agent_pages = relationship("WebAgentPageModel", back_populates="user",cascade="all, delete-orphan")
    payments = relationship("PaymentModel", back_populates="user",cascade="all, delete-orphan")
    coins_ledger = relationship("CoinsLedgerModel", back_populates="user",cascade="all, delete-orphan")
    api_keys = relationship("APIKeyModel", back_populates="user", cascade="all, delete-orphan")
    api_usage = relationship("APIDailyUsageModel", back_populates="user", cascade="all, delete-orphan")
    support_tickets = relationship("SupportTicketModel", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("UserSessionModel", back_populates="user", cascade="all, delete-orphan")
    
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
        username = username.lower()
        with db():
            return db.session.query(cls).filter(
                (func.lower(cls.username) == username) | (func.lower(cls.email) == username) | (cls.phone == username)
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


class UserSessionModel(Base):
    """A single logged-in session (one login/device) for a UnifiedAuthModel user.

    The `jti` is minted once per login and embedded in both the access and
    refresh JWTs issued for that login. It is NOT rotated on `/auth/refresh`
    calls, so a single row here represents one continuous "device session"
    from login until it is explicitly revoked (or its refresh token expires).
    This is what makes real revocation possible: `_decode_access_token_str`
    checks this table's `is_revoked` flag on every authenticated request.
    """
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False, index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    device_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("UnifiedAuthModel", back_populates="sessions")

    @classmethod
    def get_by_jti(cls, jti: str) -> Optional["UserSessionModel"]:
        with db():
            return db.session.query(cls).filter(cls.jti == jti).first()

    @classmethod
    def get_by_id_for_user(cls, session_id: int, user_id: int) -> Optional["UserSessionModel"]:
        with db():
            return db.session.query(cls).filter(cls.id == session_id, cls.user_id == user_id).first()

    @classmethod
    def list_active_for_user(cls, user_id: int) -> List["UserSessionModel"]:
        with db():
            return (
                db.session.query(cls)
                .filter(cls.user_id == user_id, cls.is_revoked == False)  # noqa: E712
                .order_by(cls.last_used_at.desc())
                .all()
            )

    @classmethod
    def create(cls, **kwargs) -> "UserSessionModel":
        with db():
            session_row = cls(**kwargs)
            db.session.add(session_row)
            db.session.commit()
            db.session.refresh(session_row)
            return session_row


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
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    user_id = Column(Integer, ForeignKey("unified_auth.id"), nullable=True)
    elevenlabs_voice_id = Column(String, nullable=True)
    has_sample_audio = Column(Boolean,nullable=True)
    audio_file = Column(String, nullable=True)
    is_enabled = Column(Boolean, default=True,server_default="true")

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    built_in_tools: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True, default={})
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True,server_default="true")
    timezone: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Static, pre-call expected LLM cost (USD per minute) for this agent's
    # selected model, refreshed from ElevenLabs' llm-usage/calculate endpoint on
    # every create/edit. This is a FLOOR estimate (config-only; ignores tool /
    # RAG runtime) used solely for the live low-balance cutoff — never for
    # billing, which reconciles against the real reported credits after a call.
    llm_price_per_minute: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ─── LLM cost calibration constants (current-config cache) ─────────────
    # Total KB pages ElevenLabs has indexed across this agent's attached
    # documents, from GET /convai/agent/{id}/knowledge-base/size. Cached here
    # (refreshed on create/edit like llm_price_per_minute) since it requires
    # an external call — re-fetching it on every conversation would be
    # wasteful. 0 when no KB is attached, None if the fetch hasn't run yet.
    kb_total_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Whether RAG is enabled for this agent's knowledge base. This platform
    # always sends rag.enabled=true to ElevenLabs on create/update (see
    # ElevenLabsAgent.create_agent), so this is currently always True — stored
    # explicitly (rather than assumed) so calibration data stays correct if a
    # toggle is ever added.
    rag_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Estimated credits needed for a hypothetical 1-minute call on this agent
    # right now, via compute_live_charge_credits(elapsed_minutes=1). Refreshed
    # after every one of the agent's calls finalizes — see
    # finalize_conversation() in app_v2/utils/conversation_lifecycle.py. Used
    # to warn a user when their balance can't cover even one more minute with
    # this specific agent (see _maybe_alert_low_agent_balance()).
    avg_credits_per_minute: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    user = relationship("UnifiedAuthModel",back_populates="agents")

    voice = relationship("VoiceModel",back_populates="agents")

    agent_ai_models = relationship("AgentAIModelBridge",back_populates="agent",cascade="all, delete-orphan")

    agent_languages = relationship("AgentLanguageBridge",back_populates="agent",cascade="all, delete-orphan")
    agent_functions = relationship("AgentFunctionBridgeModel",back_populates="agent",cascade="all, delete-orphan", order_by="AgentFunctionBridgeModel.id")
    variables = relationship("VariablesModel",back_populates="agent",cascade="all, delete-orphan")
    phone_number = relationship("PhoneNumberService",back_populates="agent")
    agent_knowledge_bases = relationship("AgentKnowledgeBaseBridge",back_populates="agent",cascade="all, delete-orphan", order_by="AgentKnowledgeBaseBridge.id")
    conversations = relationship("ConversationsModel",back_populates="agent",cascade="all, delete-orphan")
    widget = relationship("WidgetModel",back_populates="agent",cascade="all, delete-orphan")
    web_agent_pages = relationship("WebAgentPageModel",back_populates="agent",cascade="all, delete-orphan")



class AIModels(Base):

    __tablename__= "ai_models"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)
    provider: Mapped[str] = mapped_column(String,nullable=False)
    model_name: Mapped[str] = mapped_column(String,nullable=False,unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    agent_ai_models =  relationship("AgentAIModelBridge",back_populates="ai_model",cascade="all, delete-orphan")

class LanguageModel(Base):

    __tablename__ = "languages"

    id: Mapped[int] = mapped_column(Integer,autoincrement=True,index=True,primary_key=True)
    lang_code: Mapped[str] = mapped_column(String, nullable=False,unique=True)
    language: Mapped[str] = mapped_column(String,nullable=False,unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    agent_languages = relationship("AgentLanguageBridge",back_populates="language",cascade="all, delete-orphan")


class AgentAIModelBridge(Base):

    __tablename__ = "agent_ai_model_bridge"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True,index=True)
    agent_id : Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    ai_model_id: Mapped[int] = mapped_column(Integer,ForeignKey("ai_models.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    function = relationship("FunctionModel",back_populates="api_endpoint_url")


class AgentFunctionBridgeModel(Base):
    __tablename__ = "agent_function_bridge"
    id : Mapped[int] = mapped_column(Integer,primary_key =True, autoincrement=True,index=True)
    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    function_id: Mapped[int] = mapped_column(Integer,ForeignKey("functions.id"))  

    #audit fields
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    #relationships
    agent = relationship("AgentModel",back_populates="agent_functions")
    function = relationship("FunctionModel",back_populates="agent_functions")





class VariablesModel(Base):

    __tablename__ = "variables"
    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    variable_name: Mapped[str]= mapped_column(String,nullable=False)
    variable_value: Mapped[str] = mapped_column(String,nullable=False)
    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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

    # Page count ElevenLabs computed for this single document, from
    # GET /convai/knowledge-base/{document_id}. Cached here (best-effort,
    # refreshed on create/update) since it requires an external call. None
    # until the fetch has run or if it ever fails.
    num_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("UnifiedAuthModel", back_populates="knowledge_bases")
    agent_knowledge_bases = relationship("AgentKnowledgeBaseBridge",back_populates="knowledge_base",cascade="all, delete-orphan")



class AgentKnowledgeBaseBridge(Base):
    __tablename__ = "agent_knowledgebase_bridge"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,autoincrement=True)

    agent_id: Mapped[int] = mapped_column(Integer,ForeignKey("agents.id"),nullable=False)
    kb_id: Mapped[int]= mapped_column(Integer,ForeignKey("knowledge_base.id"),nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    monthly_cost: Mapped[float] = mapped_column(Float,nullable=False)

    #sid
    sid: Mapped[str] = mapped_column(String)

    # ElevenLabs phone_number_id once this number has been imported into ElevenLabs
    elevenlabs_phone_id: Mapped[str] = mapped_column(String, nullable=True)

    #relationships
    user = relationship("UnifiedAuthModel", backref="phone_numbers")
    agent = relationship("AgentModel", back_populates="phone_number")

class TwilioUserCreds(Base):
    __tablename__ = "twilio_user_creds"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer,ForeignKey("unified_auth.id"))

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_sid: Mapped[str] = mapped_column(String,nullable=False)
    auth_token: Mapped[str] = mapped_column(String,nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    elevenlabs_conv_id: Mapped[str] = mapped_column(String,nullable=True)
    # Actual TOTAL ElevenLabs cost for the call, in EL credits (from metadata.cost).
    cost: Mapped[int] = mapped_column(Integer,nullable=True)
    error_message : Mapped[str] = mapped_column(String,nullable=True)

    # ---- Cost audit columns (see app_v2/utils/cost_utils.py) ----
    # Live estimates accumulated during the call, stored in ₹ for the admin audit.
    calculated_conversation_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    calculated_llm_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    calculated_telephony_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.0)
    # Actual ElevenLabs charge split from post-call metadata, in EL credits.
    actual_llm_credits: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_conversation_credits: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # What we actually deducted from the user (their coins) and the resulting margin.
    coins_charged_to_user: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    profit_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # True when the call was cut short because the user ran out of coins mid-call
    # (call_status is failed and error_message says so). Used for filtering.
    ended_due_to_low_balance: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # ─── LLM cost calibration snapshot (captured at finalize time) ─────────
    # These freeze what the agent's cost drivers WERE when this call ran, so
    # later agent edits don't retroactively change a past call's context —
    # a per-call stand-in for real agent-version history, which doesn't exist
    # yet. See app_v2/utils/conversation_lifecycle.py finalize_conversation().
    user_message_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agent_message_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    system_prompt_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Token count (see app_v2.utils.cost_utils.count_tokens) of the system
    # prompt at call time — used only by resolve_llm_rate_basis's staleness
    # check, since token count tracks actual LLM cost far more closely than
    # character length (system_prompt_length, kept as-is for the admin UI).
    system_prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tool_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kb_total_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rag_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Which CoinUsageSettingsVersionModel snapshot was in effect when this
    # call was finalized/charged — see conversation_lifecycle.py's
    # get_or_create_current_settings_version().
    settings_version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("coin_usage_settings_versions.id"), nullable=True
    )
    total_llm_usd_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)    

    #relationships
    agent = relationship("AgentModel",back_populates="conversations")
    user = relationship("UnifiedAuthModel",back_populates="conversations")
    lead = relationship("WidgetLeadModel", back_populates="conversation", uselist=False)
    settings_version = relationship("CoinUsageSettingsVersionModel", back_populates="conversations")
class WidgetModel(Base):
    __tablename__ = "widgets"

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

    widget_name: Mapped[str] = mapped_column(String(255))
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("UnifiedAuthModel", back_populates="widgets")
    agent = relationship("AgentModel",back_populates="widget")
    leads = relationship("WidgetLeadModel", back_populates="widget",cascade="all, delete-orphan")
    web_agent_pages = relationship("WebAgentPageModel", back_populates="widget",cascade="all, delete-orphan")


class WidgetLeadModel(Base):
    __tablename__ = "widget_leads"

    id: Mapped[int] = mapped_column(primary_key=True)

    widget_id: Mapped[int] = mapped_column(
        ForeignKey("widgets.id"),
        nullable=False
    )

    conversation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("conversations.id"), nullable=True)

    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))

    custom_data: Mapped[list | None] = mapped_column(MutableList.as_mutable(JSONB))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    widget = relationship("WidgetModel", back_populates="leads")
    conversation = relationship("ConversationsModel", back_populates="lead")


class WebAgentPageModel(Base):
    __tablename__ = "web_agent_pages"

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

    widget_id: Mapped[int] = mapped_column(
        ForeignKey("widgets.id"),
        nullable=False
    )

    web_agent_name: Mapped[str] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    bg_color: Mapped[str] = mapped_column(String(20), default="#0B0B0F")

    agent_position: Mapped[str] = mapped_column(
        Enum("left", "center", "right", name="web_agent_position"),
        default="center"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("UnifiedAuthModel", back_populates="web_agent_pages")
    agent = relationship("AgentModel", back_populates="web_agent_pages")
    widget = relationship("WidgetModel", back_populates="web_agent_pages")


class ActivityLogModel(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False, index=True)
    
    event_type: Mapped[str] = mapped_column(String(100), index=True) # e.g., agent_created, call_made
    description: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True) # Renamed to avoid reserved word confusion if any
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("UnifiedAuthModel")




############## Payments and related models ##############

class PaymentModel(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False)

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR")

    status: Mapped[PaymentStatusEnum] = mapped_column(
        Enum(PaymentStatusEnum),
        default=PaymentStatusEnum.pending
    )

    provider: Mapped[PaymentProviderEnum] = mapped_column(Enum(PaymentProviderEnum), nullable=True)  # razorpay / stripe
    provider_payment_id: Mapped[str] = mapped_column(String(255), nullable=True)
    provider_order_id: Mapped[str] = mapped_column(String(255), nullable=True)

    payment_type: Mapped[PaymentTypeEnum] = mapped_column(
        Enum(PaymentTypeEnum),
        nullable=False
    )

    metadata_json: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSONB))
    invoice_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Opaque, high-entropy public identifier for this payment's invoice — shown
    # as the invoice number and used as the sole lookup key for the unauthenticated,
    # directly-navigable invoice PDF URL (see invoice_files.py). Never sequential/
    # guessable like `id`, so no separate auth token is needed on that URL.
    invoice_reference: Mapped[str] = mapped_column(String(32), nullable=True, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("UnifiedAuthModel",back_populates="payments")
    addon_order = relationship("AddOnCoinOrderModel", back_populates="payment", uselist=False)

class CoinsLedgerModel(Base):
    __tablename__ = "coins_ledger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False)

    transaction_type: Mapped[CoinTransactionTypeEnum] = mapped_column(
        Enum(CoinTransactionTypeEnum),
        nullable=False
    )

    coins: Mapped[int] = mapped_column(Integer, nullable=False)
    # positive for credit, negative for debit

    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[int | None] = mapped_column(Integer)

    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)

    # Tracks partial consumption of a credit batch across FIFO deductions.
    # Credits never expire — this is purely a drain counter, not an expiry mechanism.
    remaining_coins: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, default=0)

    # Free-text reason, populated for admin_adjustment entries (why an admin
    # added/removed coins) so it's visible in usage history — null otherwise.
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    user = relationship("UnifiedAuthModel",back_populates="coins_ledger")

class AddOnCoinOrderModel(Base):
    """
    One-time pay-as-you-go credit purchase. `amount` (rupees, user-entered)
    and `coins` (computed from `CoinUsageSettingsModel.credits_per_rupee` at
    order-creation time) are stored directly — there is no bundle to look up.
    """
    __tablename__ = "addon_coin_orders"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False)

    # Generic provider fields
    provider: Mapped[PaymentProviderEnum] = mapped_column(Enum(PaymentProviderEnum), nullable=False)
    provider_order_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_signature: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    coins: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PaymentStatusEnum] = mapped_column(Enum(PaymentStatusEnum), default=PaymentStatusEnum.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("UnifiedAuthModel")
    payment = relationship("PaymentModel", back_populates="addon_order")

class CoinUsageSettingsModel(Base):
    __tablename__ = "coin_usage_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ─── What ElevenLabs charges us ──────────────────────────────────────────
    # EL credits ElevenLabs charges us per minute of CONVERSATION (STT+TTS+turn
    # taking) — NOT LLM, which is billed separately. Drives the conversation
    # portion of the live ongoing-call cost estimate.
    elevenlabs_conversation_credits_per_minute: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # How many EL credits equal 1 USD. The ElevenLabs LLM-usage calculate
    # endpoint returns price_per_minute in USD, so this converts the stored
    # per-agent LLM price into credits for the live estimate. Admin-tuned.
    usd_to_credits: Mapped[float] = mapped_column(Float, default=10000.0, server_default="10000.0")

    # ─── What we charge our users ────────────────────────────────────────────
    # How much more (in %) we deduct from the user than what ElevenLabs
    # actually charged us for the conversation, e.g. 30 = charge 30% more
    # than the raw ElevenLabs cost. This is the sole input to actual billing.
    markup_percentage: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    # Minimum coins a user must have PER MINUTE of call before we open the
    # ElevenLabs socket. The pre-call gate requires
    # minimum_credits_per_minute × minimum_call_minutes coins.
    minimum_credits_per_minute: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # How many minutes' worth of minimum_credits_per_minute a user must be
    # able to afford before we even open the ElevenLabs socket.
    minimum_call_minutes: Mapped[int] = mapped_column(Integer, default=3, server_default="3")

    # ─── First-call safety cap & LLM cost multipliers ───────────────────────
    # Max duration (seconds) allowed for an agent's very FIRST call ever
    # (across any user) — a safety cap while a freshly configured agent's LLM
    # price / KB / tool multipliers are still unproven. 0 disables the cap.
    first_call_max_duration_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Multiplier applied to the LLM cost estimate when the agent has a
    # knowledge base attached (KB retrieval adds prompt tokens beyond
    # ElevenLabs' bare per-minute LLM price). 1.0 = no adjustment.
    knowledge_base_llm_cost_multiplier: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")

    # Multiplier applied to the LLM cost estimate when the agent has any
    # custom tool attached (tool round-trips add extra LLM calls beyond
    # ElevenLabs' bare per-minute LLM price). 1.0 = no adjustment. When an
    # agent has both a KB and tools, the higher of the two multipliers is
    # used rather than compounding them.
    tool_llm_cost_multiplier: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")

    # Pay-as-you-go purchase rate: how many coins a user receives per ₹1 paid.
    credits_per_rupee: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")

    # Minimum rupee amount a user must spend in a single pay-as-you-go
    # purchase. Admin-set (not derived) — see MIN_PURCHASE_AMOUNT in
    # schemas/coin_purchase.py for the absolute floor enforced regardless.
    minimum_purchase_amount_inr: Mapped[float] = mapped_column(Float, default=500.0, server_default="500.0")

    # Points at the CoinUsageSettingsVersionModel snapshot currently in
    # effect. A new version is created (and this repointed) only when a
    # billing-relevant field actually changes value on PUT — see
    # conversation_lifecycle.py's maybe_create_new_settings_version().
    current_version_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("coin_usage_settings_versions.id"), nullable=True
    )

    # Singleton guard: only one row can have this value
    singleton_guard: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Who last changed these settings — an admin's email, or "cron" when a
    # scheduled job (e.g. scripts/cron/conv_credits_per_min_updations.py)
    # updated a field directly. Surfaced on the admin billing-settings page.
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Per-field attribution, keyed by column name, e.g.
    # {"elevenlabs_conversation_credits_per_minute": {"updated_by": "cron", "updated_at": "2026-07-21T12:41:58+00:00"}}.
    # A single JSON column rather than an updated_by/updated_at pair per
    # tracked field, since several fields here (currently
    # elevenlabs_conversation_credits_per_minute and usd_to_credits) are each
    # updated independently by their own cron job — see scripts/cron/
    # conv_credits_per_min_updations.py and llm_cost_usd_per_min.py.
    field_update_meta: Mapped[Optional[dict]] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=True, default=dict, server_default="{}"
    )

    __table_args__ = (
        UniqueConstraint("singleton_guard", name="uq_coin_usage_settings_singleton"),
    )

    @classmethod
    def get_settings(cls):
        """Always returns the single settings record, creating it if it doesn't exist."""
        with db():
            settings = db.session.query(cls).first()
            if not settings:
                try:
                    settings = cls()
                    db.session.add(settings)
                    db.session.commit()
                    db.session.refresh(settings)
                except Exception:
                    # In case of race condition where another process created it
                    db.session.rollback()
                    settings = db.session.query(cls).first()
            return settings

class CoinUsageSettingsVersionModel(Base):
    """
    Immutable snapshot of every billing-relevant CoinUsageSettingsModel field,
    created whenever an admin actually changes one of them (see
    conversation_lifecycle.py's maybe_create_new_settings_version()). Each
    ConversationsModel row links to the version that was current when it was
    finalized, so a call's charge can always be traced back to exactly the
    rates it was computed under — without needing full agent-level version
    history, which doesn't exist yet.
    """
    __tablename__ = "coin_usage_settings_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)

    elevenlabs_conversation_credits_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    usd_to_credits: Mapped[float] = mapped_column(Float, nullable=False)
    markup_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    minimum_credits_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_call_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    first_call_max_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_base_llm_cost_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    tool_llm_cost_multiplier: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversations = relationship("ConversationsModel", back_populates="settings_version")

class APIKeyModel(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    client_secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("UnifiedAuthModel", back_populates="api_keys")

class APIDailyUsageModel(Base):
    __tablename__ = "api_daily_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False)
    usage_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)

    user = relationship("UnifiedAuthModel", back_populates="api_usage")

    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_user_daily_usage"),
    )

class APICallLogModel(Base):
    __tablename__ = "api_call_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False, index=True)
    api_route: Mapped[str] = mapped_column(String(255), nullable=False)
    # Route path TEMPLATE (e.g. "/api/v2/public/agents/{agent_id}"), not the
    # resolved path — so the Logs page groups one row per endpoint.
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=True) # in milliseconds
    coins_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    channel: Mapped[Optional[PublicLogChannelEnum]] = mapped_column(Enum(PublicLogChannelEnum, name="publiclogchannelenum"), nullable=True, index=True)
    method: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    request_params: Mapped[Optional[dict]] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True)
    request_body: Mapped[Optional[dict]] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True)
    response_body: Mapped[Optional[dict]] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True)
    is_success: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Which API key made the call — only meaningful for public_api/public_websocket
    # (widget_websocket calls aren't authenticated via an API key).
    api_key_id: Mapped[Optional[int]] = mapped_column(ForeignKey("api_keys.id"), nullable=True, index=True)

    user = relationship("UnifiedAuthModel")
    api_key = relationship("APIKeyModel")

    __table_args__ = (
        Index("ix_api_call_logs_channel_route_created", "channel", "api_route", "created_at"),
    )

class WebhookEventLogModel(Base):
    """
    Idempotent audit log for inbound webhook events.

    • Written BEFORE business logic so crashes leave a trace.
    • status transitions: received → processed | failed | duplicate
    • event_id (Razorpay webhook delivery UUID) is unique-indexed so a second
      delivery of the same event is detected instantly.
    """
    __tablename__ = "webhook_event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # e.g. "razorpay"

    event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # Razorpay's own webhook delivery ID (top-level "id" field in payload)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. "subscription.charged", "payment.captured"

    payload: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB), nullable=True)
    # Full raw payload – useful for debugging / replays

    status: Mapped[str] = mapped_column(String(20), default="received")
    # "received" | "processed" | "failed" | "duplicate"

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated when status == "failed"

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class EmailSubscriberModel(Base):
    """
    Stores email addresses collected from the public landing page.
    Each row represents one subscriber opt-in.
    """
    __tablename__ = "email_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # Human-readable source tag, e.g. "landing_page", "blog_footer"
    source: Mapped[str | None] = mapped_column(String(100), nullable=True, default="landing_page")

    # Unique token used in unsubscribe links (avoids exposing the email)
    unsubscribe_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=lambda: str(uuid.uuid4()).replace("-", "")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    subscribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportTicketModel(Base):
    """A user-submitted support ticket (bug report, billing question, account
    issue, etc.) reviewed and responded to by admins via the admin support inbox."""
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("unified_auth.id"), nullable=False, index=True)

    category: Mapped[SupportTicketCategoryEnum] = mapped_column(Enum(SupportTicketCategoryEnum), nullable=False)

    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[SupportTicketStatusEnum] = mapped_column(
        Enum(SupportTicketStatusEnum),
        nullable=False,
        default=SupportTicketStatusEnum.open,
        server_default=SupportTicketStatusEnum.open.value,
    )

    admin_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("UnifiedAuthModel", back_populates="support_tickets")