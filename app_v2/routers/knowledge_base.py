from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi_sqlalchemy import db
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
import logging
from datetime import datetime

from app_v2.databases.models import KnowledgeBaseModel, AgentModel, UnifiedAuthModel
from app_v2.schemas.knowledge_base_schema import KnowledgeBaseResponse, KnowledgeBaseURLCreate, KnowledgeBaseTextCreate, KnowledgeBaseUpdate
from app_v2.utils.jwt_utils import HTTPBearer,get_current_user
from app_v2.core.logger import setup_logger

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
                # Validate file extension
                _, ext = os.path.splitext(file.filename)
                if ext.lower() not in ALLOWED_EXTENSIONS:
                    # Depending on reqs, might skip or fail. Let's fail for now or could just skip.
                    # User probably expects all valid or fail.
                    raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

                # Validate file size
                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
                
                if file_size > MAX_FILE_SIZE:
                     raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds 10MB limit")

                file_path = os.path.join(UPLOAD_DIR, f"{agent.id}_{datetime.now().timestamp()}_{file.filename}")
                
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                
                kb_entry = KnowledgeBaseModel(
                    agent_id=agent.id,
                    kb_type="file",
                    title=file.filename,
                    content_path=file_path
                )
                db.session.add(kb_entry)
                uploaded_entries.append(kb_entry)
            
            db.session.commit()
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
            
            kb_entry = KnowledgeBaseModel(
                agent_id=agent.id,
                kb_type="url",
                content_path=url_str
            )
            db.session.add(kb_entry)
            db.session.commit()
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
            
            kb_entry = KnowledgeBaseModel(
                agent_id=agent.id,
                kb_type="text",
                title=request.title,
                content_text=request.context
            )
            db.session.add(kb_entry)
            db.session.commit()
            db.session.refresh(kb_entry)
            
            logger.info(f"Text added successfully for agent: {request.agent_name}")
            return kb_entry
            
    except HTTPException as e:
        logger.error(f"HTTP Exception during text addition: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during text addition: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/", response_model=List[KnowledgeBaseResponse], openapi_extra={"security": [{"BearerAuth": []}]})
async def get_knowledge_base(
    agent_name: str = Query(..., description="Name of the agent to retrieve KB for"),
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            agent = get_agent_by_name(agent_name, current_user.id, db.session)
            kb_entries = db.session.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.agent_id == agent.id).all()
            return kb_entries
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error retrieving knowledge base: {str(e)}")
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
