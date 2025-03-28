from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, ForeignKey, Table, create_engine
from sqlalchemy.orm import relationship, joinedload
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional, List
from fastapi_sqlalchemy import db
import bcrypt
import os, shutil
from config import MEDIA_DIR
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
import uuid

# DB_URL="postgresql://postgres:Snak3sCr1pT@localhost/voice_ninja"
DB_URL= os.getenv("DB_URL")
engine = create_engine(DB_URL, echo=False)
Base = declarative_base()


class AudioRecordModel(Base):
    __tablename__ = "audio_records"
    
    id = Column(Integer, primary_key=True)
    file_path = Column(String, nullable=True,default="")  # Store the full path to audio file
    file_name = Column(String, nullable=True,default="")  # Store the encoded filename
    duration = Column(Float, nullable=True,default=0)     # Duration in seconds
    voice = Column(String, nullable=True,default="")       # Voice type/model used
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    email = Column(String, nullable=True,default="")
    number = Column(String, nullable=True,default="")


    def get_file_url(self, request) -> str:
        """
        Get the complete URL for the audio file
        Args:
            request: FastAPI request object
        Returns:
            str: Complete URL to access the audio file
        """
        if request:
            return f"{request.base_url._url}audio/{self.file_name}/"
        return f"/audio/{self.file_name}"

    def __repr__(self):
        return f"<AudioRecord(id={self.id}, file_name={self.file_name})>"

    @classmethod
    def create_record(cls, file_path: str, file_name: str, voice: str, duration: float, email: str, number: str) -> "AudioRecordModel":
        """
        Create a new audio record
        """
        with db():
            record = cls(
                file_path=file_path,
                file_name=file_name,
                voice=voice,
                duration=duration,
                email=email,
                number=number
            )
            db.session.add(record)
            db.session.commit()
            db.session.refresh(record)
            return record

    @classmethod
    def get_by_id(cls, record_id: int) -> Optional["AudioRecordModel"]:
        """
        Get audio record by ID
        """
        with db():
            return db.session.query(cls).filter(cls.id == record_id).first()

    @classmethod
    def get_by_voice(cls, voice: str) -> List["AudioRecordModel"]:
        """
        Get all audio records for a specific voice
        """
        with db():
            return db.session.query(cls).filter(cls.voice == voice).all()

    @classmethod
    def get_recent_records(cls, limit: int = 10) -> List["AudioRecordModel"]:
        """
        Get most recent audio records
        """
        with db():
            return db.session.query(cls).order_by(cls.created_at.desc()).limit(limit).all()

    def update(self, **kwargs) -> "AudioRecordModel":
        """
        Update audio record fields
        """
        with db():
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            db.session.add(self)
            db.session.commit()
            db.session.refresh(self)
            return self

    def delete(self) -> bool:
        """
        Delete audio record
        """
        try:
            with db():
                db.session.delete(self)
                db.session.commit()
            return True
        except Exception:
            return False


class UserModel(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=True,default="")
    password = Column(String, nullable=True,default="")
    name = Column(String, nullable=True,default="")
    is_verified = Column(Boolean, nullable=True,default=False)
    last_login = Column(DateTime, nullable=True,default=func.now())
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    tokens = Column(Integer, nullable=True,default=0)
    is_admin = Column(Boolean,default=False)
    approved_domains = relationship("ApprovedDomainModel", back_populates="creator")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"
    
    @classmethod
    def get_by_id(cls, user_id: int) -> Optional["UserModel"]:
        """
        Get user by ID
        """
        with db():
            return db.session.query(cls).filter(cls.id == user_id).first()

    @classmethod
    def get_by_email(cls, email: str) -> Optional["UserModel"]:
        """
        Get user by email
        """
        with db():
            return db.session.query(cls).filter(cls.email == email).first()

    @classmethod
    def create(cls, email: str, name: str, password: str, is_verified: bool = False, tokens: int = 0) -> "UserModel":
        """
        Create a new user with hashed password
        """
        with db():
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            user = cls(email=email, name=name, password=hashed_password.decode('utf-8'), is_verified=is_verified, tokens=tokens)
            db.session.add(user)
            db.session.commit()
            db.session.refresh(user)
            return user
    
    @classmethod
    def create_admin(cls, email: str, password: str) -> "UserModel":
        """
        Create a new admin user with hashed password
        """
        with db():
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            user = cls(email=email, name="Admin", password=hashed_password.decode('utf-8'), is_verified=True, is_admin=True)
            db.session.add(user)
            db.session.commit()
            db.session.refresh(user)
            return user
        
    @classmethod
    def get_all(cls) -> List["UserModel"]:
        """
        Get all users
        """
        with db():
            return db.session.query(cls).all()

    @classmethod
    def delete(cls, user_id: int) -> bool:
        """
        Delete a user by ID
        """
        try:
            with db():
                user = db.session.query(cls).filter(cls.id == user_id).first()
                if user:
                    db.session.delete(user)
                    db.session.commit()
                return True
        except Exception:
            return False
    
    @classmethod
    def update(cls, user_id: int, **kwargs) -> "UserModel":
        """
        Update user fields
        """
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
        
    @classmethod
    def update_tokens(cls, user_id: int, new_tokens: int) -> "UserModel":
        """
        Update user's tokens
        """
        with db():
            user = db.session.query(cls).filter(cls.id == user_id).first()
            if user:
                user.tokens = new_tokens
                db.session.commit()
                db.session.refresh(user)
                return user
            return None
        
    

agent_knowledge_association = Table(
    "agent_knowledge_association",
    Base.metadata,
    Column("agent_id", Integer, ForeignKey("agents.id"), primary_key=True),
    Column("knowledge_base_id", Integer, ForeignKey("knowledge_base.id"), primary_key=True),
)


class AgentModel(Base):
    __tablename__ = "agents"
    
    id = Column(Integer, primary_key=True)
    created_by = Column(Integer, nullable=True,default=0)
    agent_name = Column(String, nullable=True,default="")
    selected_model = Column(String, nullable=True,default="")
    selected_voice = Column(String, nullable=True,default="")
    phone_number = Column(String, nullable=True,default="")
    agent_prompt = Column(String, nullable=True,default="")
    selected_language = Column(String, nullable=True,default="")
    welcome_msg = Column(String, nullable=True,default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    is_design_enabled = Column(Boolean,default=False)
    dynamic_variable = Column(JSONB, nullable=True , default={})
    max_output_tokens = Column(Integer, nullable=True,default=1000) 
    temperature = Column(Float, nullable=True,default=0.0)
    dynamic_id = Column(String, nullable=True,default=str(uuid.uuid4()))
    per_call_token_limit = Column(Integer, nullable=True,default=0)

    knowledge_base = relationship(
        "KnowledgeBaseModel",
        secondary=agent_knowledge_association,
        back_populates="agents"
    )
    audio_recordings = relationship("AudioRecordings", back_populates="agent")

    calls = relationship("CallModel", back_populates="agent", cascade="all, delete")
    custom_functions = relationship("CustomFunctionModel", back_populates="agent", cascade="all, delete-orphan")
    overall_token_limit = relationship("OverallTokenLimitModel", back_populates="agent", cascade="all, delete-orphan")
    daily_call_limit = relationship("DailyCallLimitModel", back_populates="agent", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Agent(id={self.id}, agent_name={self.agent_name})>"
    
    @classmethod
    def get_by_id(cls, agent_id: int) -> Optional["AgentModel"]:
        """
        Get agent by ID
        """
        with db():
            return db.session.query(cls).filter(cls.id == agent_id).first()
    
    @classmethod
    def get_by_dynamic_id(cls, dynamic_id: str) -> Optional["AgentModel"]:
        """
        Get agent by ID
        """
        with db():
            return db.session.query(cls).filter(cls.dynamic_id == dynamic_id).first()
        
    @classmethod
    def get_all(cls) -> List["AgentModel"]:
        """
        Get all agents
        """
        with db():
            return db.session.query(cls).all()
        
    @classmethod
    def get_all_by_user(cls, user_id: int) -> List["AgentModel"]:
        """
        Get all agents by user ID
        """
        with db():
            return db.session.query(cls).filter(cls.created_by == user_id).all()

    @classmethod
    def create(cls, agent_name: str, selected_model: str, selected_voice: str, phone_number: str, agent_prompt: str, selected_language: str, welcome_msg: str, created_by: int, temperature: float = 0.0, max_output_tokens: int = 1000, dynamic_id: str = str(uuid.uuid4())) -> "AgentModel":
        """
        Create a new agent
        """
        with db():  
            agent = cls(agent_name=agent_name, selected_model=selected_model, selected_voice=selected_voice, phone_number=phone_number, agent_prompt=agent_prompt, selected_language=selected_language, welcome_msg=welcome_msg, created_by=created_by, temperature=temperature, max_output_tokens=max_output_tokens, dynamic_id=dynamic_id)
            db.session.add(agent)
            db.session.commit()
            db.session.refresh(agent)
            return agent
        
    @classmethod
    def update(cls, agent_id: int, **kwargs) -> "AgentModel":
        """
        Update an agent by ID
        """
        with db():  
            agent = db.session.query(cls).filter(cls.id == agent_id).first()
            if agent:
                for key, value in kwargs.items():
                    if hasattr(agent, key):
                        setattr(agent, key, value)
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None
        
    @classmethod
    def delete(cls, agent_id: int) -> bool:
        """
        Delete an agent by ID
        """
        try:
            with db():
                agent = db.session.query(cls).filter(cls.id == agent_id).first()
                if agent:
                    db.session.delete(agent)
                    db.session.commit()
                return True
        except Exception:
            return False
    

    @classmethod
    def update_prompt(cls, agent_id: int, agent_prompt: str) -> "AgentModel":
        """
        Update an agent's prompt by ID
        """
        with db():
            agent = db.session.query(cls).filter(cls.id == agent_id).first()
            if agent:
                agent.agent_prompt = agent_prompt
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None
    
    @classmethod
    def update_welcome_message(cls, agent_id: int, welcome_message: str) -> "AgentModel":
        """
        Update an agent's welcome message by ID
        """
        with db():
            agent = db.session.query(cls).filter(cls.id == agent_id).first()
            if agent:
                agent.welcome_msg = welcome_message
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None
    
    @classmethod
    def update_voice(cls, agent_id: int, selected_voice: str) -> "AgentModel":
        """
        Update an agent's voice by ID
        """
        with db():
            agent = db.session.query(cls).filter(cls.id == agent_id).first()
            if agent:
                agent.selected_voice = selected_voice
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None

    @classmethod
    def update_design(cls, agent_id: int, is_enabled: bool) -> "AgentModel":
        """
        Update an agent's design by ID
        """
        with db():
            agent = db.session.query(cls).filter(cls.id == agent_id).first()
            if agent:
                agent.is_design_enabled = is_enabled
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None

    @classmethod
    def update_dynamic_variables(cls, agent_id: int, dynamic_variables: dict) -> "AgentModel":
        """
        Update dynamic variables for an agent by ID
        """
        with db():
            agent = db.session.query(cls).filter(cls.id == agent_id).first()
            if agent:
                agent.dynamic_variable = dynamic_variables
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None
    
    @classmethod
    def update_temperature_and_max_output_tokens(cls, agent_id: int, temperature: float, max_output_tokens: int) -> "AgentModel":
        """
        Update an agent's temperature and max output tokens by ID
        """
        with db():
            agent = db.session.query(cls).filter(cls.id == agent_id).first()
            if agent:
                agent.temperature = temperature
                agent.max_output_tokens = max_output_tokens
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None
    
    @classmethod
    def update_name(cls, agent_id: int, agent_name: str) -> "AgentModel":
        """
        Update an agent's name by ID
        """
        with db():
            agent = db.session.query(cls).filter(cls.id == agent_id).first()    
            if agent:
                agent.agent_name = agent_name
                db.session.commit()
                db.session.refresh(agent)
                return agent
            return None
    
class ResetPasswordModel(Base):
    __tablename__ = "reset_password"
    
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=True,default="")
    token = Column(String, nullable=True,default="")

    def __repr__(self):
        return f"<ResetPassword(id={self.id}, email={self.email})>"
    
    @classmethod
    def get_by_email(cls, email: str) -> Optional["ResetPasswordModel"]:
        """
        Get reset password by email
        """
        with db():
            return db.session.query(cls).filter(cls.email == email).first()
        
    @classmethod
    def create(cls, email: str, token: str) -> "ResetPasswordModel":
        """
        Create a new reset password record
        """
        with db():
            reset_password = cls(email=email, token=token)
            db.session.add(reset_password)
            db.session.commit() 
            db.session.refresh(reset_password)
            return reset_password
        
    @classmethod
    def delete(cls, email: str) -> bool:
        """
        Delete a reset password record by ID
        """
        try:
            with db():
                reset_password = db.session.query(cls).filter(cls.email == email).first()
                if reset_password:
                    db.session.delete(reset_password)
                    db.session.commit()
                return True
        except Exception:
            return False
    
    @classmethod
    def update_by_id(cls, reset_password_id: int, **kwargs) -> "ResetPasswordModel":
        """
        Update a reset password record by ID
        """
        with db():
            reset_password = db.session.query(cls).filter(cls.id == reset_password_id).first()
            if reset_password:
                for key, value in kwargs.items():
                    if hasattr(reset_password, key):
                        setattr(reset_password, key, value)
                db.session.commit()
                db.session.refresh(reset_password)
                return reset_password
            return None
        
    @classmethod
    def update(cls, email: str, **kwargs) -> "ResetPasswordModel":
        """
        Update a reset password record by email
        """
        with db():
            reset_password = db.session.query(cls).filter(cls.email == email).first()
            if reset_password:
                for key, value in kwargs.items():
                    if hasattr(reset_password, key):
                        setattr(reset_password, key, value)
                db.session.commit()
                db.session.refresh(reset_password)
                return reset_password
            return None
    
    @classmethod
    def update(cls, email: str, **kwargs) -> "ResetPasswordModel":
        """
        Update a reset password record by email
        """
        with db():
            reset_password = db.session.query(cls).filter(cls.email == email).first()
            if reset_password:
                for key, value in kwargs.items():
                    if hasattr(reset_password, key):
                        setattr(reset_password, key, value)
                db.session.commit()
                db.session.refresh(reset_password)
                return reset_password
            return None
    
    @classmethod
    def get_by_token(cls, token: str) -> Optional["ResetPasswordModel"]:
        """
        Get reset password by token
        """
        with db():
            return db.session.query(cls).filter(cls.token == token).first()


class KnowledgeBaseModel(Base):
    __tablename__ = "knowledge_base"

    id = Column(Integer, primary_key=True)
    created_by_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    knowledge_base_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    files = relationship("KnowledgeBaseFileModel", back_populates="knowledge_base")
    vector_path = Column(String, nullable=True,default="")
    vector_id = Column(String, nullable=True,default="")
    url = Column(String, nullable=True,default="")

    agents = relationship(
        "AgentModel",
        secondary=agent_knowledge_association,
        back_populates="knowledge_base"
    )

    def __repr__(self):
        return f"{self.knowledge_base_name} created by {self.created_by_id}"

    @classmethod
    def get_by_id(cls, knowledge_base_id: int) -> Optional["KnowledgeBaseModel"]:
        """Get knowledge base by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == knowledge_base_id).first()
        
    @classmethod
    def get_all(cls) -> List["KnowledgeBaseModel"]:
        """Get all knowledge base records"""
        with db():
            return db.session.query(cls).all()

    @classmethod
    def get_by_name(cls, knowledge_base_name: str, created_by_id: int) -> Optional["KnowledgeBaseModel"]:
        """Get knowledge base by name"""
        with db():
            return db.session.query(cls).filter(cls.knowledge_base_name == knowledge_base_name, cls.created_by_id == created_by_id).first()

    @classmethod
    def create(cls, knowledge_base_name: str, created_by_id: int, url: str = "") -> "KnowledgeBaseModel":
        """Create a new knowledge base record"""
        # Create knowledge_base_files directory if it doesn't exist
        knowledge_base_dir = os.path.join(MEDIA_DIR, "knowledge_base_files")
        if not os.path.exists(knowledge_base_dir):
            os.makedirs(knowledge_base_dir)
            
        with db():
            knowledge_base = cls(
                knowledge_base_name=knowledge_base_name,
                created_by_id=created_by_id,
                url=url
            )
            db.session.add(knowledge_base)
            db.session.commit()
            db.session.refresh(knowledge_base)
            return knowledge_base

    @classmethod
    def update(cls, knowledge_base_id: int, **kwargs) -> Optional["KnowledgeBaseModel"]:
        """Update a knowledge base record"""
        with db():
            knowledge_base = db.session.query(cls).filter(cls.id == knowledge_base_id).first()
            if knowledge_base:
                for key, value in kwargs.items():
                    if hasattr(knowledge_base, key):
                        setattr(knowledge_base, key, value)
                db.session.commit()
                db.session.refresh(knowledge_base)
                return knowledge_base
            return None

    @classmethod 
    def delete(cls, knowledge_base_id: int) -> bool:
        """Delete a knowledge base record and its associated file"""
        try:
            with db():
                knowledge_base = db.session.query(cls).filter(cls.id == knowledge_base_id).first()
                if knowledge_base:
                    files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
                    for file in files:
                        # Delete files from directory
                        obj = KnowledgeBaseFileModel.get_by_id(file.id)
                        file_dir = os.path.join(MEDIA_DIR, str(obj.file_path))
                        if os.path.exists(file_dir):
                            os.remove(file_dir)
                        KnowledgeBaseFileModel.delete(file.id)
                    # Delete the knowledge base record directly
                    db.session.delete(knowledge_base)
                    db.session.commit()
                    return True
                return False
        except Exception as e:
            print(f"Error deleting knowledge base: {str(e)}")
            return False
    
    @classmethod    
    def get_all_by_user(cls, user_id: int) -> List["KnowledgeBaseModel"]:
        """Get all knowledge base records by user ID"""
        with db():
            return db.session.query(cls).filter(cls.created_by_id == user_id).all()
        
    @classmethod
    def update_name(cls, knowledge_base_id: int, new_name: str) -> Optional["KnowledgeBaseModel"]:
        """Update the name of a knowledge base record"""
        with db():
            knowledge_base = db.session.query(cls).filter(cls.id == knowledge_base_id).first()
            if knowledge_base:
                knowledge_base.knowledge_base_name = new_name
                db.session.commit()
                db.session.refresh(knowledge_base)
                return knowledge_base
            return None


class AudioRecordings(Base):
    __tablename__ = "audio_recordings"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey('agents.id', ondelete='CASCADE'))
    audio_name = Column(String, nullable=False)
    audio_file = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    call_id = Column(String, nullable=True)

    # Relationship
    agent = relationship("AgentModel", back_populates="audio_recordings")

    def __str__(self):
        return f"{self.agent.agent_name} audio recording"

    @classmethod
    def create(cls, agent_id: int, audio_file: str, audio_name: str, created_at: datetime, call_id: str = None) -> "AudioRecordings":
        """Create a new audio recording"""

        with db():
            recording = cls(
                agent_id=agent_id,
                audio_file=audio_file,
                audio_name=audio_name,
                created_at=created_at,
                call_id=call_id
            )
            db.session.add(recording)
            db.session.commit()
            db.session.refresh(recording)
            return recording

    @classmethod
    def get_by_id(cls, recording_id: int) -> Optional["AudioRecordings"]:
        """Get recording by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == recording_id).first()
    
    @classmethod
    def get_all_by_agent(cls, agent_id: int) -> List["AudioRecordings"]:
        """Get all audio recordings by agent ID"""
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).all()
    
    @classmethod
    def get_all_by_user(cls, user_id: int) -> List["AudioRecordings"]:
        """Get all audio recordings by user ID"""
        with db():
            return db.session.query(cls).filter(cls.agent.created_by == user_id).all()

    @classmethod
    def get_by_call_id(cls, call_id: str) -> Optional["AudioRecordings"]:
        """Get recording by call ID"""
        with db():
            audio_model = db.session.query(cls).filter(cls.call_id == call_id).first()
            return audio_model
    
    @classmethod    
    def delete(cls, recording_id: int) -> bool:
        """Delete an audio recording by ID and remove the file from directory"""
        try:
            with db():
                recording = db.session.query(cls).filter(cls.id == recording_id).first()
                if recording:
                    # Remove the audio file from directory
                    audio_file_path = recording.audio_file
                    if os.path.exists(audio_file_path):
                        os.remove(audio_file_path)
                    
                    # Delete record from database
                    db.session.delete(recording)
                    db.session.commit()
                    return True
            return False
        except Exception as e:
            print(f"Error deleting audio recording: {str(e)}")
            return False

    
# class AgentPhoneNumberModel(Base):
#     __tablename__ = "agent_phone_number"

#     id = Column(Integer, primary_key=True)
#     name = Column(String, nullable=False)
#     agent_id = Column(Integer, nullable=True)
#     phone_number = Column(String, nullable=False)
#     created_by_id = Column(Integer, nullable=True)
#     created_at = Column(DateTime, default=func.now())
#     updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

#     created_by = relationship("UserModel", back_populates="created_phone_numbers", foreign_keys=[created_by_id])
#     agent = relationship("AgentModel", back_populates="phone_numbers")
#     def __repr__(self):
#         return f"<AgentPhoneNumber(id={self.id}, phone_number={self.phone_number})>"

#     @classmethod
#     def get_all_by_agent(cls, agent_id: int) -> List["AgentPhoneNumberModel"]:
#         """Get all phone numbers by agent ID"""
#         with db():
#             return db.session.query(cls).filter(cls.agent_id == agent_id).all()
        
#     @classmethod
#     def get_all_by_user(cls, user_id: int) -> List["AgentPhoneNumberModel"]:
#         """Get all phone numbers by user ID"""
#         with db():
#             return db.session.query(cls).filter(cls.created_by_id == user_id).all()

class AgentConnectionModel(Base):
    """Model for tracking connections between agents"""
    __tablename__ = "agent_connections"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, nullable=False)
    icon_url = Column(String, default="/static/Web/images/gif-icon-3.gif")
    primary_color = Column(String, default="#8338ec")
    secondary_color = Column(String, default="#5e60ce") 
    pulse_color = Column(String, default="rgba(131, 56, 236, 0.3)")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


    def __repr__(self):
        return f"<AgentConnection(id={self.id}, agent_id={self.agent_id})>"
    
    @classmethod
    def create(cls, agent_id: int) -> "AgentConnectionModel":
        """Create a new agent connection"""
        with db():
            connection = cls(agent_id=agent_id)
            db.session.add(connection)
            db.session.commit()
            return connection

    @classmethod
    def create_connection(cls, agent_id: int, icon_url: str, primary_color: str, secondary_color: str, pulse_color: str) -> "AgentConnectionModel":
        """Create a new agent connection"""
        with db():
            connection = cls(agent_id=agent_id, icon_url=icon_url, primary_color=primary_color, secondary_color=secondary_color, pulse_color=pulse_color)
            db.session.add(connection)
            db.session.commit()
            return connection   
    
    @classmethod
    def get_by_agent_id(cls, agent_id: int) -> Optional["AgentConnectionModel"]:
        """Get connection by agent ID"""
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).first()
    
    @classmethod
    def update_connection(cls, agent_id: int, **kwargs) -> "AgentConnectionModel":  
        """Update a connection by agent ID"""
        with db():
            connection = db.session.query(cls).filter(cls.agent_id == agent_id).first()
            if connection:
                for key, value in kwargs.items():
                    if hasattr(connection, key):
                        setattr(connection, key, value)
                db.session.commit()
                db.session.refresh(connection)
                return connection
            return None
        
    @classmethod
    def delete_connection(cls, agent_id: int) -> bool:
        """Delete a connection by agent ID"""
        with db():
            connection = db.session.query(cls).filter(cls.agent_id == agent_id).first()
            if connection:
                db.session.delete(connection)
                db.session.commit()
                return True
            return False
        


class PaymentModel(Base):
    __tablename__ = "payments"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    order_id = Column(String, nullable=True,default="")
    payment_id = Column(String, nullable=True,default="")
    amount = Column(Integer, nullable=True,default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Payment(id={self.id}, for user_id={self.user_id}, amount={self.amount})>"
    

    @classmethod
    def create(cls, user_id: int, order_id: str, payment_id: str, amount: int) -> "PaymentModel":
        """Create a new payment record"""
        with db():
            payment = cls(user_id=user_id, order_id=order_id, payment_id=payment_id, amount=amount)
            db.session.add(payment)
            db.session.commit()
            return payment
    
    @classmethod
    def get_by_user_id(cls, user_id: int) -> List["PaymentModel"]:
        """Get all payments by user ID"""
        with db():
            return db.session.query(cls).filter(cls.user_id == user_id).all()
        
    @classmethod
    def get_by_order_id(cls, order_id: str) -> Optional["PaymentModel"]:
        """Get payment by order ID"""
        with db():
            return db.session.query(cls).filter(cls.order_id == order_id).first()



class AdminTokenModel(Base):
    __tablename__ = "admin_tokens"

    id = Column(Integer, primary_key=True)
    token_values = Column(Integer, nullable=True, default=0)
    free_tokens = Column(Integer, nullable=True, default=0)


    @classmethod
    def ensure_default_exists(cls) -> "AdminTokenModel":
        """
        Ensures that a default admin token record exists.
        Returns the default record (either existing or newly created).
        """
        with db():
            default_token = cls.get_by_id(1)
            if not default_token:
                default_token = cls.create()  # Uses default values (id=1, token_values=0)
            return default_token

    def __repr__(self):
        return f"<AdminToken(id={self.id}, token_values={self.token_values})>"
    
    @classmethod
    def update_token_values(cls, id: int, token_values: int) -> Optional["AdminTokenModel"]:
        """Update admin token values"""
        with db():
            admin_token = db.session.query(cls).filter(cls.id == id).first()
            if admin_token:
                admin_token.token_values = token_values
                db.session.commit()
                db.session.refresh(admin_token)
                return admin_token
            return None
    
    @classmethod
    def update_free_tokens(cls, id: int, free_tokens: int) -> Optional["AdminTokenModel"]:
        """Update admin free tokens"""
        with db():
            admin_token = db.session.query(cls).filter(cls.id == id).first()
            if admin_token:
                admin_token.free_tokens = free_tokens
                db.session.commit()
                db.session.refresh(admin_token)
                return admin_token
            return None
    
    @classmethod
    def get_by_id(cls, id: int) -> Optional["AdminTokenModel"]:
        """Get admin token by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == id).first()
        
    @classmethod
    def create(cls, id: int = 1, token_values: int = 0, free_tokens: int = 0) -> "AdminTokenModel":
        """Create a new admin token record with default id=1 and token_values=0"""
        with db():
            # Check if record exists first
            existing = cls.get_by_id(id)
            if existing:
                return existing
            
            # Create new record if it doesn't exist
            admin_token = cls(id=id, token_values=token_values, free_tokens=free_tokens)
            db.session.add(admin_token)
            db.session.commit()
            return admin_token

class TokensToConsume(Base):
    __tablename__ = "tokens_to_consume"

    id = Column(Integer, primary_key=True)
    token_values = Column(Integer, nullable=True, default=0)

    def __repr__(self):
        return f"<TokensToConsume(id={self.id}, token_values={self.token_values})>"
    
    @classmethod
    def ensure_default_exists(cls) -> "TokensToConsume":
        """
        Ensures that a default tokens to consume record exists.
        Returns the default record (either existing or newly created).
        """
        with db():
            default_token = cls.get_by_id(1)
            if not default_token:
                default_token = cls.create() 
            return default_token
    
    @classmethod
    def get_by_id(cls, id: int) -> Optional["TokensToConsume"]:
        """Get tokens to consume by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == id).first()
        
    @classmethod
    def create(cls, id: int = 1, token_values: int = 0) -> "TokensToConsume":
        """Create a new tokens to consume record with default id=1 and token_values=0"""
        with db():
            tokens_to_consume = cls(id=id, token_values=token_values)
            db.session.add(tokens_to_consume)
            db.session.commit()
            return tokens_to_consume
        
    @classmethod
    def update_token_values(cls, id: int, token_values: int) -> Optional["TokensToConsume"]:
        """Update tokens to consume values"""
        with db():
            tokens_to_consume = db.session.query(cls).filter(cls.id == id).first()
            if tokens_to_consume:
                tokens_to_consume.token_values = token_values
                db.session.commit()
                db.session.refresh(tokens_to_consume)
                return tokens_to_consume
            return None


class KnowledgeBaseFileModel(Base):
    __tablename__ = "knowledge_base_files"

    id = Column(Integer, primary_key=True)
    knowledge_base_id = Column(Integer, ForeignKey('knowledge_base.id', ondelete='CASCADE'))
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    text_content = Column(String, nullable=True)

    knowledge_base = relationship("KnowledgeBaseModel", back_populates="files")

    def __repr__(self):
        return f"<KnowledgeBaseFile(id={self.id}, file_name={self.file_name})>"

    @classmethod
    def get_by_id(cls, file_id: int) -> Optional["KnowledgeBaseFileModel"]:
        """Get file by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == file_id).first()  
    
    @classmethod
    def get_all_by_knowledge_base(cls, knowledge_base_id: int) -> List["KnowledgeBaseFileModel"]:
        """Get all files by knowledge base ID"""
        with db():
            return db.session.query(cls).filter(cls.knowledge_base_id == knowledge_base_id).all()

    @classmethod
    def create(cls, knowledge_base_id: int, file_name: str, file_path: str, text_content: str) -> "KnowledgeBaseFileModel":
        """Create a new knowledge base file"""
        with db():
            file = cls(knowledge_base_id=knowledge_base_id, file_name=file_name, file_path=file_path, text_content=text_content)
            db.session.add(file)
            db.session.commit()
            db.session.refresh(file)
            return file

    @classmethod
    def delete(cls, file_id: int) -> bool:
        """Delete a knowledge base file by ID"""
        with db():
            file = db.session.query(cls).filter(cls.id == file_id).first()
            if file:
                db.session.delete(file)
                db.session.commit()
                return True
            return False

class CallModel(Base):
    __tablename__ = "calls"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"))
    call_id = Column(String, unique=True, nullable=False)  # Unique identifier for each call
    variables = Column(JSONB, nullable=True, default={})  # Store variables as JSON
    created_at = Column(DateTime, default=func.now())

    # Relationship with AgentModel
    agent = relationship("AgentModel", back_populates="calls")

    @classmethod
    def get_by_id(cls, call_id: int) -> Optional["CallModel"]:
        """Get call by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == call_id).first()

    @classmethod
    def get_by_agent_id(cls, agent_id: int) -> Optional["CallModel"]:
        """Get call by agent ID"""
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).first()

    @classmethod
    def create(cls, agent_id: int, call_id: str, variables: dict) -> "CallModel":
        """Create a new call"""
        with db():
            call = cls(agent_id=agent_id, call_id=call_id, variables=variables)
            db.session.add(call)
            db.session.commit()
            db.session.refresh(call)
            return call

    @classmethod
    def update(cls, call_id: int, variables: dict) -> Optional["CallModel"]:
        """Update a call"""
        with db():
            call = db.session.query(cls).filter(cls.id == call_id).first()
            if call:
                call.variables = variables
                db.session.commit()
                db.session.refresh(call)
                return call
            return None

    @classmethod
    def delete(cls, call_id: int) -> bool:
        """Delete a call"""
        with db():
            call = db.session.query(cls).filter(cls.id == call_id).first()
            if call:
                db.session.delete(call)
                db.session.commit()
                return True
            return False


class WebhookModel(Base):
    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True)
    webhook_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)

    def __repr__(self):
        return f"<Webhook(id={self.id}, webhook_url={self.webhook_url})>"
    
    @classmethod
    def get_by_id(cls, id: int) -> Optional["WebhookModel"]:
        """Get webhook by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == id).first()
    
    @classmethod
    def get_all_by_user(cls, user_id: int) -> List["WebhookModel"]:
        """Get all webhooks by user ID"""
        with db():
            return db.session.query(cls).filter(cls.created_by == user_id).all()
    

    @classmethod
    def get_by_user(cls, user_id: int) -> Optional["WebhookModel"]:
        """Get webhook by user ID"""
        with db():
            return db.session.query(cls).filter(cls.created_by == user_id).order_by(cls.created_at.desc()).first()
    
    @classmethod
    def create(cls, webhook_url: str, created_by: int) -> "WebhookModel":
        """Create a new webhook"""
        with db():
            webhook = cls(webhook_url=webhook_url, created_by=created_by)
            db.session.add(webhook)
            db.session.commit()
            db.session.refresh(webhook)
            return webhook

    @classmethod
    def get_is_active_by_id(cls, id: int) -> Optional["WebhookModel"]:
        """Get webhook by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == id, cls.is_active == True).first()

    @classmethod
    def get_all(cls) -> List["WebhookModel"]:
        """Get all webhooks"""
        with db():
            return db.session.query(cls).all()
    
    @classmethod
    def check_webhook_exists(cls, webhook_url: str, user_id: int) -> bool:
        """Check if webhook URL already exists for the user"""
        with db():
            webhook = db.session.query(cls).filter(
                cls.webhook_url == webhook_url,
                cls.created_by == user_id
            ).first()
            return bool(webhook)
        
    @classmethod
    def delete(cls, id: int) -> bool:
        """Delete a webhook"""
        with db():
            webhook = db.session.query(cls).filter(cls.id == id).first()
            if webhook:
                db.session.delete(webhook)
                db.session.commit()
                return True
            return False
    
    @classmethod
    def update_webhook_url(cls, id: int, webhook_url: str) -> Optional["WebhookModel"]:
        """Update a webhook URL"""
        with db():
            webhook = db.session.query(cls).filter(cls.id == id).first()
            if webhook:
                webhook.webhook_url = webhook_url
                db.session.commit()
                db.session.refresh(webhook)
                return webhook
            return None
    

class CustomFunctionModel(Base):
    __tablename__ = "custom_functions"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey('agents.id', ondelete='CASCADE'))
    function_name = Column(String, nullable=False)
    function_description = Column(String, nullable=False)
    function_url = Column(String, nullable=False)
    function_timeout = Column(Integer, nullable=True)
    function_parameters = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    agent = relationship("AgentModel", back_populates="custom_functions")

    def __repr__(self):
        return f"<CustomFunction(id={self.id}, function_name={self.function_name})>"
    
    @classmethod
    def get_by_id(cls, id: int) -> Optional["CustomFunctionModel"]:
        """Get custom function by ID"""
        with db():
            return db.session.query(cls).filter(cls.id == id).first()
    
    @classmethod
    def get_all_by_agent(cls, agent_id: int) -> List["CustomFunctionModel"]:
        """Get all custom functions by agent ID"""
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).all()
        
    @classmethod
    def create(
        cls, 
        agent_id: int, 
        function_name: str, 
        function_description: str, 
        function_url: str, 
        function_timeout: Optional[int] = None,  # Optional with default None
        function_parameters: Optional[dict] = None  # Optional with default None
    ) -> "CustomFunctionModel":
        """Create a new custom function with optional fields"""

        # Ensure function_parameters is a valid JSONB format
        if function_parameters is None:
            function_parameters = {}

        with db():
            function = cls(
                agent_id=agent_id, 
                function_name=function_name, 
                function_description=function_description, 
                function_url=function_url, 
                function_timeout=function_timeout, 
                function_parameters=function_parameters
            )
            db.session.add(function)
            db.session.commit()
            db.session.refresh(function)
            return function
        
    @classmethod
    def delete(cls, id: int) -> bool:
        """Delete a custom function"""
        with db():
            function = db.session.query(cls).filter(cls.id == id).first()
            if function:
                db.session.delete(function)
                db.session.commit()
                return True
            return False

    @classmethod
    def get_all_by_agent_id(cls, agent_id: int) -> List["CustomFunctionModel"]:
        """Get all custom functions by agent ID"""
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).all() 
        
    @classmethod
    def get_by_name(cls, function_name: str, agent_id: int) -> Optional["CustomFunctionModel"]:
        """Get custom function by name"""
        with db():
            return db.session.query(cls).filter(cls.function_name == function_name, cls.agent_id == agent_id).first()


class ApprovedDomainModel(Base):
    __tablename__ = "approved_domains"

    id = Column(Integer, primary_key=True)
    domain = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)  # ✅ Ensure ForeignKey exists
    creator = relationship("UserModel", back_populates="approved_domains")  # Must match

    def __repr__(self):
        return f"<ApprovedDomain(id={self.id}, domain={self.domain})>"
    
    @classmethod
    def get_all(cls) -> List["ApprovedDomainModel"]:
        """Get all approved domains"""
        with db():
            return db.session.query(cls).all()
        
    @classmethod
    def create(cls, domain: str, created_by: int) -> "ApprovedDomainModel":
        """Create a new approved domain"""
        with db():
            domain = cls(domain=domain, created_by=created_by)
            db.session.add(domain)
            db.session.commit()
            db.session.refresh(domain)
            return domain
    
    @classmethod
    def get_all_by_user(cls, user_id: int) -> List["ApprovedDomainModel"]:
        """Get all approved domains by user ID"""
        with db():
            return db.session.query(cls).filter(cls.created_by == user_id).order_by(cls.created_at.desc()).all()
    
    @classmethod
    def check_domain_exists(cls, domain: str, user_id: int) -> bool:
        """Check if a domain exists for a user"""
        with db():
            return db.session.query(cls).filter(cls.domain == domain, cls.created_by == user_id).first() is not None
    
    @classmethod
    def delete(cls, id: int) -> bool:
        """Delete an approved domain"""
        with db():
            domain = db.session.query(cls).filter(cls.id == id).first()
            if domain:
                db.session.delete(domain)
                db.session.commit()
                return True
            return False


class ConversationModel(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    transcript = Column(JSONB, nullable=True)
    summary = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    audio_recording_id = Column(Integer, ForeignKey("audio_recordings.id"), nullable=False)

    def __repr__(self):
        return f"<Conversation(id={self.id})>"
    
    @classmethod
    def create(cls, audio_recording_id: int, transcript: List[str], summary: Optional[str] = None) -> "ConversationModel":
        with db():
            conversation = cls(transcript=transcript, summary=summary, audio_recording_id=audio_recording_id)
            db.session.add(conversation)
            db.session.commit()
            db.session.refresh(conversation)
            return conversation
        
    @classmethod
    def get_by_audio_recording_id(cls, audio_recording_id: int) -> Optional["ConversationModel"]:
        with db():
            return db.session.query(cls).filter(cls.audio_recording_id == audio_recording_id).first()
        
    @classmethod
    def get_all(cls) -> List["ConversationModel"]:
        with db():
            return db.session.query(cls).all()
        
    @classmethod
    def delete(cls, id: int) -> bool:
        with db():
            conversation = db.session.query(cls).filter(cls.id == id).first()
            if conversation:
                db.session.delete(conversation)
                db.session.commit()
                return True
            return False

    @classmethod
    def update_summary(cls, id: int, summary: str) -> bool:
        with db():
            conversation = db.session.query(cls).filter(cls.id == id).first()
            if conversation:
                conversation.summary = summary
                db.session.commit()
                db.session.refresh(conversation)
                return conversation
            return False

class OverallTokenLimitModel(Base):
    __tablename__ = "overall_token_limit"
    
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    overall_token_limit = Column(Integer, nullable=True, default=0)
    last_used_tokens = Column(Integer, nullable=True, default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationship with Agent
    agent = relationship("AgentModel", back_populates="overall_token_limit")

    def __repr__(self):
        return f"<OverallTokenLimit(id={self.id}, agent_id={self.agent_id})>"

    @classmethod
    def create(cls, agent_id: int, overall_token_limit: int, last_used_tokens: int) -> "OverallTokenLimitModel":
        """Create a new overall token limit record"""
        with db():
            overall_token_limit = cls(agent_id=agent_id, overall_token_limit=overall_token_limit, last_used_tokens=last_used_tokens)
            db.session.add(overall_token_limit)
            db.session.commit()
            db.session.refresh(overall_token_limit)
            return overall_token_limit

    @classmethod
    def get_by_agent_id(cls, agent_id: int) -> Optional["OverallTokenLimitModel"]:
        """Get overall token limit by agent ID"""
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).first()

    @classmethod
    def update(cls, agent_id: int, overall_token_limit: Optional[int] = None, last_used_tokens: Optional[int] = None) -> "OverallTokenLimitModel":
        """Update overall token limit record"""
        with db():
            overall_token_limit = db.session.query(cls).filter(cls.agent_id == agent_id).first()
            if overall_token_limit:
                if overall_token_limit is not None:
                    overall_token_limit.overall_token_limit = overall_token_limit
                if last_used_tokens is not None:
                    overall_token_limit.last_used_tokens = last_used_tokens
                db.session.commit()
                db.session.refresh(overall_token_limit)
                return overall_token_limit
            return None

    @classmethod
    def delete(cls, agent_id: int) -> bool:
        """Delete overall token limit record"""
        try:
            with db():
                overall_token_limit = db.session.query(cls).filter(cls.agent_id == agent_id).first()
                if overall_token_limit:
                    db.session.delete(overall_token_limit)
                    db.session.commit()
                return True
        except Exception:
            return False


class DailyCallLimitModel(Base):
    """Daily Call Limit Model"""
    __tablename__ = "daily_call_limit"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), unique=True)
    set_value = Column(Integer, nullable=False)
    last_used = Column(Integer, nullable=False, default=0)
    last_updated = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationship with Agent
    agent = relationship("AgentModel", back_populates="daily_call_limit")

    def __repr__(self):
        return f"<DailyCallLimit(id={self.id}, agent_id={self.agent_id})>"

    @classmethod
    def create(cls, agent_id: int, set_value: int, last_used: int = 0) -> "DailyCallLimitModel":
        """Create a new daily call limit record"""
        with db():
            daily_call_limit = cls(
                agent_id=agent_id,
                set_value=set_value,
                last_used=last_used,
                last_updated=datetime.utcnow()
            )
            db.session.add(daily_call_limit)
            db.session.commit()
            db.session.refresh(daily_call_limit)
            return daily_call_limit

    @classmethod
    def get_by_agent_id(cls, agent_id: int) -> Optional["DailyCallLimitModel"]:
        """Get daily call limit by agent ID"""
        with db():
            return db.session.query(cls).filter(cls.agent_id == agent_id).first()

    @classmethod
    def update(cls, agent_id: int, set_value: Optional[int] = None, last_used: Optional[int] = None) -> "DailyCallLimitModel":
        """Update daily call limit record"""
        with db():
            daily_call_limit = db.session.query(cls).filter(cls.agent_id == agent_id).first()
            if daily_call_limit:
                if set_value is not None:
                    daily_call_limit.set_value = set_value
                if last_used is not None:
                    daily_call_limit.last_used = last_used
                daily_call_limit.last_updated = datetime.utcnow()
                db.session.commit()
                db.session.refresh(daily_call_limit)
                return daily_call_limit
            return None

    @classmethod
    def delete(cls, agent_id: int) -> bool:
        """Delete daily call limit record"""
        try:
            with db():
                daily_call_limit = db.session.query(cls).filter(cls.agent_id == agent_id).first()
                if daily_call_limit:
                    db.session.delete(daily_call_limit)
                    db.session.commit()
                return True
        except Exception:
            return False


Base.metadata.create_all(engine)

