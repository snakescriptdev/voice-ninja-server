from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi_sqlalchemy import db
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
import logging
from datetime import datetime
from app_v2.schemas.pagination import PaginatedResponse
import math

from app_v2.databases.models import KnowledgeBaseModel, AgentModel, UnifiedAuthModel
from app_v2.schemas.knowledge_base_schema import KnowledgeBaseResponse, KnowledgeBaseURLCreate, KnowledgeBaseTextCreate, KnowledgeBaseUpdate
from app_v2.utils.jwt_utils import HTTPBearer,get_current_user
from app_v2.core.logger import setup_logger
from app_v2.utils.elevenlabs import ElevenLabsKB, ElevenLabsAgent

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/knowledge-base",
    tags=["Knowledge Base"],
    dependencies=[Depends(HTTPBearer())]
)

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt"}

def get_agent_by_name(agent_name: str, user_id: int, db_session: Session) -> AgentModel:
    agent = db_session.query(AgentModel).filter(AgentModel.agent_name == agent_name, AgentModel.user_id == user_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@router.post("/upload", response_model=List[KnowledgeBaseResponse], openapi_extra={"security": [{"BearerAuth": []}]}, status_code=status.HTTP_201_CREATED)
async def upload_files(
    agent_name: str = Form(...),
    files: List[UploadFile] = File(...),
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        uploaded_entries = []
        with db():
            agent = get_agent_by_name(agent_name, current_user.id, db.session)
            
            for file in files:
                # ... (validation logic)
                _, ext = os.path.splitext(file.filename)
                if ext.lower() not in ALLOWED_EXTENSIONS:
                    raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
                
                if file_size > MAX_FILE_SIZE:
                     raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds 10MB limit")
                
                if file_size == 0:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")

                file_path = os.path.join(UPLOAD_DIR, f"{agent.id}_{datetime.now().timestamp()}_{file.filename}")
                
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                
                # ---- ElevenLabs KB Upload ----
                elevenlabs_document_id = None
                try:
                    logger.info(f"Syncing file '{file.filename}' to ElevenLabs KB for agent '{agent_name}'")
                    kb_client = ElevenLabsKB()
                    kb_response = kb_client.upload_document(file_path, name=file.filename)
                    
                    if kb_response.status:
                        elevenlabs_document_id = kb_response.data.get("document_id")
                    else:
                        logger.warning(f"Failed to upload to ElevenLabs KB: {kb_response.error_message}")
                        # Clean up local file on failure if we want strict sync
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        raise HTTPException(status_code=424, detail=f"ElevenLabs KB upload failed: {kb_response.error_message}")
                except HTTPException:
                    raise
                except Exception as e:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    logger.error(f"Error syncing with ElevenLabs: {e}")
                    raise HTTPException(status_code=424, detail="Error syncing with ElevenLabs")

                kb_entry = KnowledgeBaseModel(
                    agent_id=agent.id,
                    kb_type="file",
                    title=file.filename,
                    content_path=file_path,
                    elevenlabs_document_id=elevenlabs_document_id
                )
                db.session.add(kb_entry)
                uploaded_entries.append(kb_entry)
            
            db.session.commit()
            
            # ---- Attach to Agent in ElevenLabs ----
            if agent.elevenlabs_agent_id:
                try:
                    agent_client = ElevenLabsAgent()
                    # Get all existing KB items for this agent to update the config
                    all_kb = db.session.query(KnowledgeBaseModel).filter(
                        KnowledgeBaseModel.agent_id == agent.id,
                        KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
                    ).all()
                    
                    kb_docs = [
                        {"id": item.elevenlabs_document_id, "type": "file", "name": item.title}
                        for item in all_kb
                    ]
                    
                    agent_client.update_agent(
                        agent_id=agent.elevenlabs_agent_id,
                        knowledge_base=kb_docs
                    )
                    logger.info(f"Updated ElevenLabs agent {agent.elevenlabs_agent_id} with {len(kb_docs)} documents")
                except Exception as e:
                    logger.error(f"Failed to attach KB to ElevenLabs agent: {e}")

            for entry in uploaded_entries:
                db.session.refresh(entry)
            
            logger.info(f"{len(uploaded_entries)} files uploaded successfully for agent: {agent_name}")
            return uploaded_entries

    except HTTPException as e:
        logger.error(f"HTTP Exception during file upload: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during file upload: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/url", response_model=KnowledgeBaseResponse,openapi_extra={"security": [{"BearerAuth": []}]},status_code=status.HTTP_201_CREATED)
async def add_url(request: KnowledgeBaseURLCreate, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        
        url_str = str(request.url)
        
        # Fetch title
        with db():
            agent = get_agent_by_name(request.agent_name, current_user.id, db.session)
            
            # ---- ElevenLabs KB Sync ----
            elevenlabs_document_id = None
            try:
                logger.info(f"Syncing URL '{url_str}' to ElevenLabs KB")
                kb_client = ElevenLabsKB()
                kb_response = kb_client.add_url_document(url_str)
                
                if kb_response.status:
                    elevenlabs_document_id = kb_response.data.get("document_id")
                else:
                    raise HTTPException(status_code=424, detail=f"ElevenLabs KB URL addition failed: {kb_response.error_message}")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error syncing URL with ElevenLabs: {e}")
                raise HTTPException(status_code=424, detail="Error syncing with ElevenLabs")

            kb_entry = KnowledgeBaseModel(
                agent_id=agent.id,
                kb_type="url",
                content_path=url_str,
                elevenlabs_document_id=elevenlabs_document_id
            )
            db.session.add(kb_entry)
            db.session.commit()
            
            # ---- Attach to Agent in ElevenLabs ----
            if agent.elevenlabs_agent_id:
                try:
                    agent_client = ElevenLabsAgent()
                    all_kb = db.session.query(KnowledgeBaseModel).filter(
                        KnowledgeBaseModel.agent_id == agent.id,
                        KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
                    ).all()
                    
                    kb_docs = []
                    for item in all_kb:
                        doc_type = "url" if item.kb_type == "url" else "file" if item.kb_type == "file" else "text"
                        kb_docs.append({"id": item.elevenlabs_document_id, "type": doc_type, "name": item.title or item.content_path})

                    agent_client.update_agent(
                        agent_id=agent.elevenlabs_agent_id,
                        knowledge_base=kb_docs
                    )
                except Exception as e:
                    logger.error(f"Failed to attach KB URL to ElevenLabs agent: {e}")

            db.session.refresh(kb_entry)
            
            logger.info(f"URL added successfully for agent: {request.agent_name}")
            return kb_entry
            
    except HTTPException as e:
        logger.error(f"HTTP Exception during URL addition: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during URL addition: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/text", response_model=KnowledgeBaseResponse,openapi_extra={"security": [{"BearerAuth": []}]},status_code=status.HTTP_201_CREATED)
async def add_text(request: KnowledgeBaseTextCreate, current_user: UnifiedAuthModel = Depends(get_current_user)):
    try:
        with db():
            agent = get_agent_by_name(request.agent_name, current_user.id, db.session)
            
            # ---- ElevenLabs KB Sync ----
            elevenlabs_document_id = None
            try:
                logger.info(f"Syncing text '{request.title}' to ElevenLabs KB")
                kb_client = ElevenLabsKB()
                kb_response = kb_client.add_text_document(request.context, request.title)
                
                if kb_response.status:
                    elevenlabs_document_id = kb_response.data.get("document_id")
                else:
                    raise HTTPException(status_code=424, detail=f"ElevenLabs KB text addition failed: {kb_response.error_message}")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error syncing text with ElevenLabs: {e}")
                raise HTTPException(status_code=424, detail="Error syncing with ElevenLabs")

            kb_entry = KnowledgeBaseModel(
                agent_id=agent.id,
                kb_type="text",
                title=request.title,
                content_text=request.context,
                elevenlabs_document_id=elevenlabs_document_id
            )
            db.session.add(kb_entry)
            db.session.commit()
            
            # ---- Attach to Agent in ElevenLabs ----
            if agent.elevenlabs_agent_id:
                try:
                    agent_client = ElevenLabsAgent()
                    all_kb = db.session.query(KnowledgeBaseModel).filter(
                        KnowledgeBaseModel.agent_id == agent.id,
                        KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
                    ).all()
                    
                    kb_docs = []
                    for item in all_kb:
                        doc_type = "text" if item.kb_type == "text" else "file" if item.kb_type == "file" else "url"
                        kb_docs.append({"id": item.elevenlabs_document_id, "type": doc_type, "name": item.title or "Untitled"})

                    agent_client.update_agent(
                        agent_id=agent.elevenlabs_agent_id,
                        knowledge_base=kb_docs
                    )
                except Exception as e:
                    logger.error(f"Failed to attach KB text to ElevenLabs agent: {e}")

            db.session.refresh(kb_entry)
            
            logger.info(f"Text added successfully for agent: {request.agent_name}")
            return kb_entry
            
    except HTTPException as e:
        logger.error(f"HTTP Exception during text addition: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during text addition: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/", response_model=PaginatedResponse[KnowledgeBaseResponse], openapi_extra={"security": [{"BearerAuth": []}]})
async def get_all_knowledge_base(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        if page < 1:
            page = 1
        
        skip = (page - 1) * size

        with db():
            # Query all KB items for agents belonging to the current user
            query = (
                db.session.query(KnowledgeBaseModel)
                .join(AgentModel)
                .filter(AgentModel.user_id == current_user.id)
            )
            
            total = query.count()
            pages = math.ceil(total / size)

            kb_entries = (
                query
                .offset(skip)
                .limit(size)
                .all()
            )
            
            return PaginatedResponse(
                total=total,
                page=page,
                size=size,
                pages=pages,
                items=kb_entries
            )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error retrieving user knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/agent/{agent_id}", response_model=List[KnowledgeBaseResponse], openapi_extra={"security": [{"BearerAuth": []}]})
async def get_agent_knowledge_base(
    agent_id: int,
    skip: int = 0,
    limit: int = 20,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            # Verify agent ownership
            agent = (
                db.session.query(AgentModel)
                .filter(
                    AgentModel.id == agent_id,
                    AgentModel.user_id == current_user.id
                )
                .first()
            )
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

            # Fetch KB items for this specific agent
            kb_entries = (
                db.session.query(KnowledgeBaseModel)
                .filter(KnowledgeBaseModel.agent_id == agent_id)
                .offset(skip)
                .limit(limit)
                .all()
            )
            return kb_entries
            
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error retrieving agent knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT, openapi_extra={"security": [{"BearerAuth": []}]})
async def delete_knowledge_base_item(
    kb_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            # Join with Agent to ensure ownership
            kb_entry = db.session.query(KnowledgeBaseModel).join(AgentModel).filter(
                KnowledgeBaseModel.id == kb_id,
                AgentModel.user_id == current_user.id
            ).first()
            
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Knowledge base item not found")
            
            agent = kb_entry.agent
            
            # ---- Update Agent in ElevenLabs (detach doc FIRST) ----
            if agent and agent.elevenlabs_agent_id:
                try:
                    agent_client = ElevenLabsAgent()
                    # Query all valid KB items for this agent, excluding the one we are about to delete
                    all_kb = db.session.query(KnowledgeBaseModel).filter(
                        KnowledgeBaseModel.agent_id == agent.id,
                        KnowledgeBaseModel.elevenlabs_document_id.isnot(None),
                        KnowledgeBaseModel.id != kb_id  # Exclude current item
                    ).all()
                    
                    kb_docs = []
                    for item in all_kb:
                        doc_type = "file" if item.kb_type == "file" else "url" if item.kb_type == "url" else "text"
                        kb_docs.append({"id": item.elevenlabs_document_id, "type": doc_type, "name": item.title or "Untitled"})

                    agent_client.update_agent(
                        agent_id=agent.elevenlabs_agent_id,
                        knowledge_base=kb_docs
                    )
                    logger.info(f"Detached KB item {kb_id} from agent {agent.elevenlabs_agent_id}")
                except Exception as e:
                    logger.error(f"Failed to update ElevenLabs agent before KB deletion: {e}")
                    # Decide if we should proceed or partial fail. 
                    # If we fail to detach, the delete below will likely fail too. 
                    # But we should probably try anyway or warn.
            
            # ---- ElevenLabs KB Sync (Delete from Library SECOND) ----
            if kb_entry.elevenlabs_document_id:
                try:
                    logger.info(f"Deleting document {kb_entry.elevenlabs_document_id} from ElevenLabs KB")
                    kb_client = ElevenLabsKB()
                    kb_client.delete_document(kb_entry.elevenlabs_document_id)
                except Exception as e:
                    logger.error(f"Failed to delete document from ElevenLabs KB: {e}")

            # Delete file if exists
            if kb_entry.kb_type == "file" and kb_entry.content_path and os.path.exists(kb_entry.content_path):
                try:
                    os.remove(kb_entry.content_path)
                except OSError as e:
                    logger.warning(f"Failed to delete file {kb_entry.content_path}: {e}")

            db.session.delete(kb_entry)
            db.session.commit()
            
            logger.info(f"Deleted KB item {kb_id}")
            return
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error deleting knowledge base item: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{kb_id}", response_model=KnowledgeBaseResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def update_knowledge_base_item(
    kb_id: int,
    update_data: KnowledgeBaseUpdate,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
             # Join with Agent to ensure ownership
            kb_entry = db.session.query(KnowledgeBaseModel).join(AgentModel).filter(
                KnowledgeBaseModel.id == kb_id,
                AgentModel.user_id == current_user.id
            ).first()
            
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Knowledge base item not found")
            
            if update_data.title is not None:
                kb_entry.title = update_data.title
            
            if update_data.content_text is not None:
                if kb_entry.kb_type == "text":
                    kb_entry.content_text = update_data.content_text
                else:
                    # User trying to update text content of a file/url type?
                    # Depending on reqs. Text content usually applies to text type.
                    # We will only allow updating text content for text type for now to be safe,
                    # or if the user wants to essentially convert it? No, keep it simple.
                     pass 

            db.session.commit()
            db.session.refresh(kb_entry)
            return kb_entry
            
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating knowledge base item: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
