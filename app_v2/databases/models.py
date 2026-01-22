from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, ForeignKey, Table, create_engine, Enum, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional, List, Dict
from fastapi_sqlalchemy import db
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
    
    voices = relationship("VoiceModel", back_populates="user")

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
    
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    agent_id = Column(Integer,ForeignKey("agents.id"),nullable=True)
    elevenlabs_voice_id = Column(String, nullable=True)
    audio_file = Column(String, nullable=True)
    
    user = relationship("UserModel", back_populates="voices")
    agent = relationship("AgentModel",back_populates="voices")

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

    @classmethod
    def set_for_agent(cls, agent_id: int, voice_id: int):
        with db():
            existing = db.session.query(cls).filter(cls.agent_id == agent_id).first()
            if existing:
                existing.voice_id = voice_id
            else:
                existing = cls(agent_id=agent_id, voice_id=voice_id)
                db.session.add(existing)

            db.session.commit()
            db.session.refresh(existing)
            return existing

Base.metadata.create_all(engine)




class AgentModel(Base):
    '''
    The agent model that lists all the agents existing.
    Each agent has the following Attributes.
    Attributes:
                -id (int,primaryKey)
                -agent_name (string,required)
                -user_id (foreignKey): 
                        description: this attribute track the user who created the agent
                -created_at (datetime)
                -updated_at (date_time)
                -first_message (string):
                        description: the message agent uses to greet at begining
                -system_prompt (string,required)
                        description: the role assigned to the agent and its limitatios (like salesPerson)
    '''
    __tablename__ = "agents"

    #attributes for the agebt model
    
    id = Column(Integer,primary_key=True,index=True,autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"),nullable=False)


    agent_name = Column(String,nullable=False)
    first_message = Column(String,nullable=True)
    system_prompt = Column(String,nullable=False,)
    
    # meta data attributes
    created_at = Column(DateTime, default=func.now())
    updated_at =Column(DateTime,default=func.now(),updated_at=func.now())

    #relationship 
    user = relationship("UserModel",backref="agents")
    voice = relationship("VoiceModel",back_populates="agents",uselist=False)
    ai_model = relationship("AgentAIModel",back_populates="agent",uselist=False)
    language = relationship("AgentLanguage", uselist=False, back_populates="agent")
    functions = relationship("AgentFunctions",back_populates="agent")
    variables = relationship("AgentVariables",back_populates="agent")

    


    #methodds
    @classmethod
    def create(
        cls,
        user_id: int,
        agent_name: str,
        system_prompt: str = "",
        first_message: str = "") -> "AgentModel":
        """
        the create method create the instance of agent to store it in db
        :params
            -user_id: id of user who creates the agent
            -agent_name: name of the agent created
            -system_prompt: the role assigned to the agent
            -first_message: the greeting message for the agent

        """
        with db():
            agent = cls(
                user_id=user_id,
                agent_name=agent_name,
                system_prompt=system_prompt,
                first_message=first_message
            )
            db.session.add(agent)
            db.session.commit()
            db.session.refresh(agent)
            return agent
        
    @classmethod
    def get_by_id(cls, agent_id: int) -> Optional["AgentModel"]:
        with db():
            return db.session.query(cls).filter(cls.id == agent_id).first()

    @classmethod
    def get_by_user(cls, user_id: int) -> List["AgentModel"]:
        with db():
            return db.session.query(cls).filter(cls.user_id == user_id).all()

    @classmethod
    def get_user_agent(
        cls,
        agent_id: int,
        user_id: int
    ) -> Optional["AgentModel"]:
        """Fetch agent only if it belongs to the user"""
        with db():
            return db.session.query(cls).filter(
                cls.id == agent_id,
                cls.user_id == user_id
            ).first()

    @classmethod
    def update(
        cls,
        agent_id: int,
        user_id: int,
        **kwargs
    ) -> Optional["AgentModel"]:
        with db():
            agent = db.session.query(cls).filter(
                cls.id == agent_id,
                cls.user_id == user_id
            ).first()

            if not agent:
                return None

            for key, value in kwargs.items():
                if hasattr(agent, key):
                    setattr(agent, key, value)

            db.session.commit()
            db.session.refresh(agent)
            return agent

    @classmethod
    def delete(cls, agent_id: int, user_id: int) -> bool:
        with db():
            agent = db.session.query(cls).filter(
                cls.id == agent_id,
                cls.user_id == user_id
            ).first()

            if not agent:
                return False

            db.session.delete(agent)
            db.session.commit()
            return True

    @classmethod
    def exists(cls, agent_id: int) -> bool:
        with db():
            return db.session.query(
                db.session.query(cls).filter(cls.id == agent_id).exists()
            ).scalar()

    @classmethod
    def count_by_user(cls, user_id: int) -> int:
        with db():
            return db.session.query(cls).filter(
                cls.user_id == user_id
            ).count()
        


class AgentAIModel(Base):
    __tablename__ = "agent_ai_models"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)

    model_name = Column(String, nullable=False)  # e.g. gpt-4o, gpt-4.1, claude, etc
    provider = Column(String, nullable=False)    # openai, anthropic, etc

    created_at = Column(DateTime, default=func.now())

    agent = relationship("AgentModel", back_populates="ai_model")

    @classmethod
    def set_for_agent(cls, agent_id: int, model_name: str, provider: str):
        with db():
            existing = db.session.query(cls).filter(cls.agent_id == agent_id).first()
            if existing:
                existing.model_name = model_name
                existing.provider = provider
            else:
                existing = cls(
                    agent_id=agent_id,
                    model_name=model_name,
                    provider=provider
                )
                db.session.add(existing)

            db.session.commit()
            db.session.refresh(existing)
            return existing

    @classmethod
    def get_by_agent(cls, agent_id: int):
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).first()




class AgentLanguage(Base):
    __tablename__ = "agent_languages"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)

    language_code = Column(String, nullable=False)  # en, es, fr, hi
    language_name = Column(String, nullable=False)

    agent = relationship("AgentModel", back_populates="language")


    @classmethod
    def set_for_agent(cls, agent_id: int, language_code: str, language_name: str):
        with db():
            existing = db.session.query(cls).filter(cls.agent_id == agent_id).first()
            if existing:
                existing.language_code = language_code
                existing.language_name = language_name
            else:
                existing = cls(
                    agent_id=agent_id,
                    language_code=language_code,
                    language_name=language_name
                )
                db.session.add(existing)

            db.session.commit()
            db.session.refresh(existing)
            return existing







class AgentFunction(Base):
    __tablename__ = "agent_functions"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)

    function_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    function_schema = Column(Text, nullable=True)  # JSON schema as string

    created_at = Column(DateTime, default=func.now())

    agent = relationship("AgentModel", back_populates="functions")


    @classmethod
    def add(cls, agent_id: int, function_name: str, description: str = "", function_schema: str = ""):
        with db():
            fn = cls(
                agent_id=agent_id,
                function_name=function_name,
                description=description,
                function_schema=function_schema
            )
            db.session.add(fn)
            db.session.commit()
            db.session.refresh(fn)
            return fn

    @classmethod
    def list_by_agent(cls, agent_id: int):
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).all()

    @classmethod
    def delete(cls, function_id: int, agent_id: int) -> bool:
        with db():
            fn = db.session.query(cls).filter(
                cls.id == function_id,
                cls.agent_id == agent_id
            ).first()
            if not fn:
                return False
            db.session.delete(fn)
            db.session.commit()
            return True


class AgentVariable(Base):
    __tablename__ = "agent_variables"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)

    key = Column(String, nullable=False)
    value = Column(Text, nullable=True)

    agent = relationship("AgentModel", back_populates="variables")


    @classmethod
    def set(cls, agent_id: int, key: str, value: str):
        with db():
            existing = db.session.query(cls).filter(
                cls.agent_id == agent_id,
                cls.key == key
            ).first()

            if existing:
                existing.value = value
            else:
                existing = cls(agent_id=agent_id, key=key, value=value)
                db.session.add(existing)

            db.session.commit()
            db.session.refresh(existing)
            return existing

    @classmethod
    def get_all(cls, agent_id: int):
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).all()