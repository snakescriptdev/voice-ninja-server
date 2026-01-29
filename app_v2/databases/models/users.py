from sqlalchemy import Column, DateTime, String, Integer,Boolean, ForeignKey
from sqlalchemy.sql import func
from app_v2.databases.base import Base
from sqlalchemy.orm import relationship, Session
from fastapi_sqlalchemy import db
from typing import Optional




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
    agents = relationship("AgentModel",back_populates="user",cascade="all, delete-orphan")

    @classmethod
    def get_by_id(
        cls,
        session: Session,
        user_id: int
    ) -> Optional["UserModel"]:
        return (
            session
            .query(cls)
            .filter(cls.id == user_id)
            .first()
        )

    @classmethod
    def get_by_email(
        cls,
        session: Session,
        email: str
    ) -> Optional["UserModel"]:
        return (
            session
            .query(cls)
            .filter(cls.email == email)
            .first()
        )

    @classmethod
    def get_by_username(
        cls,
        session: Session,
        username: str
    ) -> Optional["UserModel"]:
        return (
            session
            .query(cls)
            .filter(
                (cls.email == username) |
                (cls.phone == username)
            )
            .first()
        )

    # --- UPDATE METHOD ---

    @classmethod
    def update(
        cls,
        session: Session,
        user_id: int,
        **kwargs
    ) -> Optional["UserModel"]:
        user = (
            session
            .query(cls)
            .filter(cls.id == user_id)
            .first()
        )

        if not user:
            return None

        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)

        session.commit()
        session.refresh(user)
        return user
    


class OAuthProviderModel(Base):
    __tablename__ = "oauth_providers"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String, nullable=False)
    provider_user_id = Column(String, nullable=False)
    email = Column(String, nullable=False)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now()
    )

    user = relationship("UserModel", backref="oauth_providers")

    # ---------- READ ----------

    @classmethod
    def get_by_provider_and_user_id(
        cls,
        session: Session,
        provider: str,
        provider_user_id: str
    ) -> Optional["OAuthProviderModel"]:
        return (
            session
            .query(cls)
            .filter(
                cls.provider == provider,
                cls.provider_user_id == provider_user_id
            )
            .first()
        )

    @classmethod
    def get_by_provider_and_email(
        cls,
        session: Session,
        provider: str,
        email: str
    ) -> Optional["OAuthProviderModel"]:
        return (
            session
            .query(cls)
            .filter(
                cls.provider == provider,
                cls.email == email
            )
            .first()
        )

    # ---------- CREATE ----------

    @classmethod
    def create(
        cls,
        session: Session,
        user_id: int,
        provider: str,
        provider_user_id: str,
        email: str
    ) -> "OAuthProviderModel":
        oauth_provider = cls(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            email=email
        )

        session.add(oauth_provider)
        session.commit()
        session.refresh(oauth_provider)

        return oauth_provider



class UnifiedAuthModel(Base):
    """Unified authentication model that tracks all user authentication methods."""

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

    # OTP auth
    has_otp_auth = Column(Boolean, default=False)
    otp_code = Column(String, nullable=True, default="")
    otp_expires_at = Column(DateTime, nullable=True)

    # Google auth
    has_google_auth = Column(Boolean, default=False)
    google_user_id = Column(String, nullable=True, default="")

    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # ---------- READ ----------

    @classmethod
    def get_by_id(
        cls,
        session: Session,
        user_id: int
    ) -> Optional["UnifiedAuthModel"]:
        return (
            session
            .query(cls)
            .filter(cls.id == user_id)
            .first()
        )

    @classmethod
    def get_by_email(
        cls,
        session: Session,
        email: str
    ) -> Optional["UnifiedAuthModel"]:
        return (
            session
            .query(cls)
            .filter(cls.email == email)
            .first()
        )

    @classmethod
    def get_by_phone(
        cls,
        session: Session,
        phone: str
    ) -> Optional["UnifiedAuthModel"]:
        return (
            session
            .query(cls)
            .filter(cls.phone == phone)
            .first()
        )

    @classmethod
    def get_by_username(
        cls,
        session: Session,
        username: str
    ) -> Optional["UnifiedAuthModel"]:
        return (
            session
            .query(cls)
            .filter(
                (cls.email == username) |
                (cls.phone == username)
            )
            .first()
        )

    @classmethod
    def get_by_google_id(
        cls,
        session: Session,
        google_user_id: str
    ) -> Optional["UnifiedAuthModel"]:
        return (
            session
            .query(cls)
            .filter(cls.google_user_id == google_user_id)
            .first()
        )

    # ---------- CREATE ----------

    @classmethod
    def create(
        cls,
        session: Session,
        **kwargs
    ) -> "UnifiedAuthModel":
        user = cls(**kwargs)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    # ---------- UPDATE ----------

    @classmethod
    def update(
        cls,
        session: Session,
        user_id: int,
        **kwargs
    ) -> Optional["UnifiedAuthModel"]:
        user = (
            session
            .query(cls)
            .filter(cls.id == user_id)
            .first()
        )

        if not user:
            return None

        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)

        session.commit()
        session.refresh(user)
        return user
    

class AdminTokenModel(Base):
    __tablename__ = "admin_tokens"

    id = Column(Integer, primary_key=True)
    token_values = Column(Integer, nullable=True, default=0)
    free_tokens = Column(Integer, nullable=True, default=0)

    @classmethod
    def ensure_default_exists(
        cls,
        session: Session
    ) -> "AdminTokenModel":
        default_token = (
            session
            .query(cls)
            .filter(cls.id == 1)
            .first()
        )

        if not default_token:
            default_token = cls(
                id=1,
                token_values=0,
                free_tokens=0
            )
            session.add(default_token)
            session.commit()
            session.refresh(default_token)

        return default_token


class TokensToConsume(Base):
    __tablename__ = "tokens_to_consume"

    id = Column(Integer, primary_key=True)
    token_values = Column(Integer, nullable=True, default=0)

    @classmethod
    def ensure_default_exists(
        cls,
        session: Session
    ) -> "TokensToConsume":
        default_token = (
            session
            .query(cls)
            .filter(cls.id == 1)
            .first()
        )

        if not default_token:
            default_token = cls(
                id=1,
                token_values=0
            )
            session.add(default_token)
            session.commit()
            session.refresh(default_token)

        return default_token
