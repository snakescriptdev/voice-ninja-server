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

from app_v2.databases.models import KnowledgeBaseModel, AgentModel, UnifiedAuthModel, AgentKnowledgeBaseBridge
from app_v2.schemas.knowledge_base_schema import (
    KnowledgeBaseResponse, 
    KnowledgeBaseURLCreate, 
    KnowledgeBaseTextCreate, 
    KnowledgeBaseFileUpdate,
    KnowledgeBaseURLUpdate,
    KnowledgeBaseTextUpdate,
    KnowledgeBaseBind
)
from app_v2.utils.jwt_utils import HTTPBearer,get_current_user
from app_v2.core.logger import setup_logger
from app_v2.utils.elevenlabs import ElevenLabsKB, ElevenLabsAgent
from app_v2.utils.scraping_utils import scrape_webpage_title

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/knowledge-base",
    tags=["Knowledge Base"],
    dependencies=[Depends(HTTPBearer())]
)

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt"}

def sync_agent_kb(agent_id: int):
    """
    Consolidates synchronization of an agent's knowledge base with ElevenLabs.
    """
    try:
        with db():
            agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
            if not agent or not agent.elevenlabs_agent_id:
                return

            # Fetch all KBs associated with this agent via bridge table
            all_kb = (
                db.session.query(KnowledgeBaseModel)
                .join(AgentKnowledgeBaseBridge)
                .filter(AgentKnowledgeBaseBridge.agent_id == agent_id, KnowledgeBaseModel.elevenlabs_document_id.isnot(None))
                .all()
            )

            kb_docs = []
            for item in all_kb:
                doc_type = "file" if item.kb_type == "file" else "url" if item.kb_type == "url" else "text"
                kb_docs.append({
                    "id": item.elevenlabs_document_id,
                    "name": item.title or "Untitled",
                    "type": doc_type,
                    "usage_mode": "auto"
                })

            agent_client = ElevenLabsAgent()
            agent_client.update_agent(
                agent_id=agent.elevenlabs_agent_id,
                knowledge_base=kb_docs
            )
            logger.info(f"Successfully synced ElevenLabs agent {agent.elevenlabs_agent_id} with {len(kb_docs)} KB items")
    except Exception as e:
        logger.error(f"Failed to sync KB with ElevenLabs agent {agent_id}: {e}")

@router.post("/upload", response_model=List[KnowledgeBaseResponse], openapi_extra={"security": [{"BearerAuth": []}]}, status_code=status.HTTP_201_CREATED)
async def upload_files(
    files: List[UploadFile] = File(...),
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        uploaded_entries = []
        with db():
            for file in files:
                # validation logic
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

                file_path = os.path.join(UPLOAD_DIR, f"{current_user.id}_{datetime.now().timestamp()}_{file.filename}")
                
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                
                # ---- ElevenLabs KB Upload ----
                elevenlabs_document_id = None
                rag_index_id = None
                try:
                    logger.info(f"Syncing file '{file.filename}' to ElevenLabs KB for user '{current_user.email}'")
                    kb_client = ElevenLabsKB()
                    kb_response = kb_client.upload_document(file_path, name=file.filename)
                    
                    if kb_response.status:
                        elevenlabs_document_id = kb_response.data.get("document_id")
                        # ---- Compute RAG Index ----
                        rag_index_id = kb_client.compute_rag_index(elevenlabs_document_id)
                    else:
                        logger.warning(f"Failed to upload to ElevenLabs KB: {kb_response.error_message}")
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
                    user_id=current_user.id,
                    kb_type="file",
                    title=file.filename,
                    content_path=file_path,
                    elevenlabs_document_id=elevenlabs_document_id,
                    rag_index_id=rag_index_id,
                    file_size=round((file_size /1024),2)    #file size in Kb
                )
                db.session.add(kb_entry)
                uploaded_entries.append(kb_entry)
            
            db.session.commit()
            
            for entry in uploaded_entries:
                db.session.refresh(entry)
            
            logger.info(f"{len(uploaded_entries)} files uploaded successfully for user: {current_user.email}")
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
        with db():
            # ---- ElevenLabs KB Sync ----
            elevenlabs_document_id = None
            rag_index_id = None
            try:
                logger.info(f"Syncing URL '{url_str}' to ElevenLabs KB")
                kb_client = ElevenLabsKB()
                kb_response = kb_client.add_url_document(url_str)
                
                if kb_response.status:
                    elevenlabs_document_id = kb_response.data.get("document_id")
                    # ---- Compute RAG Index ----
                    rag_index_id = kb_client.compute_rag_index(elevenlabs_document_id)
                else:
                    raise HTTPException(status_code=424, detail=f"ElevenLabs KB URL addition failed: {kb_response.error_message}")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error syncing URL with ElevenLabs: {e}")
                raise HTTPException(status_code=424, detail="Error syncing with ElevenLabs")
            
            # ---- Scrape Webpage Title ----
            title = scrape_webpage_title(url_str)


            kb_entry = KnowledgeBaseModel(
                user_id=current_user.id,
                kb_type="url",
                content_path=url_str,
                elevenlabs_document_id=elevenlabs_document_id,
                rag_index_id=rag_index_id,
                title=title
            )
            db.session.add(kb_entry)
            db.session.commit()
            
            db.session.refresh(kb_entry)
            logger.info(f"URL added successfully for user: {current_user.email}")
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
            # ---- ElevenLabs KB Sync ----
            elevenlabs_document_id = None
            rag_index_id = None
            try:
                logger.info(f"Syncing text '{request.title}' to ElevenLabs KB")
                kb_client = ElevenLabsKB()
                kb_response = kb_client.add_text_document(request.content, request.title)
                
                if kb_response.status:
                    elevenlabs_document_id = kb_response.data.get("document_id")
                    # ---- Compute RAG Index ----
                    rag_index_id = kb_client.compute_rag_index(elevenlabs_document_id)
                else:
                    raise HTTPException(status_code=424, detail=f"ElevenLabs KB text addition failed: {kb_response.error_message}")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error syncing text with ElevenLabs: {e}")
                raise HTTPException(status_code=424, detail="Error syncing with ElevenLabs")

            kb_entry = KnowledgeBaseModel(
                user_id=current_user.id,
                kb_type="text",
                title=request.title,
                content_text=request.content,
                elevenlabs_document_id=elevenlabs_document_id,
                rag_index_id=rag_index_id
            )
            db.session.add(kb_entry)
            db.session.commit()
            
            db.session.refresh(kb_entry)
            logger.info(f"Text added successfully for user: {current_user.email}")
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
            # Query all KB items belonging to the current user
            query = (
                db.session.query(KnowledgeBaseModel)
                .filter(KnowledgeBaseModel.user_id == current_user.id)
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


@router.get("/agent/{agent_id}", response_model=PaginatedResponse[KnowledgeBaseResponse], openapi_extra={"security": [{"BearerAuth": []}]})
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

            # Fetch KB items associated with this agent via bridge table
            query = (
                db.session.query(KnowledgeBaseModel)
                .join(AgentKnowledgeBaseBridge)
                .filter(AgentKnowledgeBaseBridge.agent_id == agent_id)
            )

            total = query.count()
            
            kb_entries = (
                query
                .offset(skip)
                .limit(limit)
                .all()
            )

            pages = math.ceil(total / limit) if limit > 0 else 1
            current_page = (skip // limit) + 1 if limit > 0 else 1
            
            return PaginatedResponse(
                total=total,
                page=current_page,
                size=limit,
                pages=pages,
                items=kb_entries
            )
            
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
            kb_entry = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id == kb_id,
                KnowledgeBaseModel.user_id == current_user.id
            ).first()
            
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Knowledge base item not found")
            
            # Find all agents this KB is attached to
            bridges = db.session.query(AgentKnowledgeBaseBridge).filter(AgentKnowledgeBaseBridge.kb_id == kb_id).all()
            agent_ids = [bridge.agent_id for bridge in bridges]

            # ---- ElevenLabs KB Sync (Delete from Library FIRST) ----
            if kb_entry.elevenlabs_document_id:
                try:
                    kb_client = ElevenLabsKB()
                    logger.info(f"Deleting document {kb_entry.elevenlabs_document_id} from ElevenLabs KB")
                    kb_client.delete_document(kb_entry.elevenlabs_document_id)
                except Exception as e:
                    logger.error(f"Failed to delete document from ElevenLabs KB: {e}")

            # Delete file if exists
            if kb_entry.kb_type == "file" and kb_entry.content_path and os.path.exists(kb_entry.content_path):
                try:
                    os.remove(kb_entry.content_path)
                except OSError as e:
                    logger.warning(f"Failed to delete file {kb_entry.content_path}: {e}")

            # Delete bridge entries first
            for bridge in bridges:
                db.session.delete(bridge)
                
            db.session.delete(kb_entry)
            db.session.commit()

            # ---- Update Agents in ElevenLabs (Sync AFTER deletion) ----
            for agent_id in agent_ids:
                sync_agent_kb(agent_id)
            
            logger.info(f"Deleted KB item {kb_id} and synced agents")
            return
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error deleting knowledge base item: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{kb_id}/file", response_model=KnowledgeBaseResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def update_file_knowledge_base(
    kb_id: int,
    update_data: KnowledgeBaseFileUpdate,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            kb_entry = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id == kb_id,
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "file"
            ).first()
            
            if not kb_entry:
                raise HTTPException(status_code=404, detail="File Knowledge base item not found")
            
            if update_data.title is not None and update_data.title != kb_entry.title:
                kb_entry.title = update_data.title
                if kb_entry.elevenlabs_document_id:
                    ElevenLabsKB().update_document_name(kb_entry.elevenlabs_document_id, kb_entry.title)

            db.session.commit()
            db.session.refresh(kb_entry)

            # Sync agents
            bridges = db.session.query(AgentKnowledgeBaseBridge).filter(AgentKnowledgeBaseBridge.kb_id == kb_id).all()
            for bridge in bridges:
                sync_agent_kb(bridge.agent_id)

            return kb_entry
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating file KB: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{kb_id}/url", response_model=KnowledgeBaseResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def update_url_knowledge_base(
    kb_id: int,
    update_data: KnowledgeBaseURLUpdate,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            kb_entry = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id == kb_id,
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "url"
            ).first()
            
            if not kb_entry:
                raise HTTPException(status_code=404, detail="URL Knowledge base item not found")
            
            needs_resync = False
            if update_data.title is not None and update_data.title != kb_entry.title:
                kb_entry.title = update_data.title
                if kb_entry.elevenlabs_document_id:
                    ElevenLabsKB().update_document_name(kb_entry.elevenlabs_document_id, kb_entry.title)

            if update_data.url is not None and str(update_data.url) != kb_entry.content_path:
                kb_entry.content_path = str(update_data.url)
                needs_resync = True

            if needs_resync:
                # Delete old doc and upload new URL
                kb_client = ElevenLabsKB()
                if kb_entry.elevenlabs_document_id:
                    kb_client.delete_document(kb_entry.elevenlabs_document_id)
                
                kb_response = kb_client.add_url_document(kb_entry.content_path, name=kb_entry.title)
                if kb_response.status:
                    kb_entry.elevenlabs_document_id = kb_response.data.get("document_id")
                    # Compute new RAG index
                    kb_entry.rag_index_id = kb_client.compute_rag_index(kb_entry.elevenlabs_document_id)
                else:
                    logger.error(f"Failed to re-sync URL KB: {kb_response.error_message}")

            db.session.commit()
            db.session.refresh(kb_entry)

            # Sync agents
            bridges = db.session.query(AgentKnowledgeBaseBridge).filter(AgentKnowledgeBaseBridge.kb_id == kb_id).all()
            for bridge in bridges:
                sync_agent_kb(bridge.agent_id)

            return kb_entry
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating URL KB: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{kb_id}/text", response_model=KnowledgeBaseResponse, openapi_extra={"security": [{"BearerAuth": []}]})
async def update_text_knowledge_base(
    kb_id: int,
    update_data: KnowledgeBaseTextUpdate,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            kb_entry = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id == kb_id,
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "text"
            ).first()
            
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Text Knowledge base item not found")
            
            needs_resync = False
            if update_data.title is not None and update_data.title != kb_entry.title:
                kb_entry.title = update_data.title
                if kb_entry.elevenlabs_document_id:
                    ElevenLabsKB().update_document_name(kb_entry.elevenlabs_document_id, kb_entry.title)

            if update_data.content_text is not None and update_data.content_text != kb_entry.content_text:
                kb_entry.content_text = update_data.content_text
                needs_resync = True

            if needs_resync:
                kb_client = ElevenLabsKB()
                if kb_entry.elevenlabs_document_id:
                    kb_client.delete_document(kb_entry.elevenlabs_document_id)
                
                kb_response = kb_client.add_text_document(kb_entry.content_text, name=kb_entry.title)
                if kb_response.status:
                    kb_entry.elevenlabs_document_id = kb_response.data.get("document_id")
                    # Compute new RAG index
                    kb_entry.rag_index_id = kb_client.compute_rag_index(kb_entry.elevenlabs_document_id)
                else:
                    logger.error(f"Failed to re-sync text KB: {kb_response.error_message}")

            db.session.commit()
            db.session.refresh(kb_entry)

            # Sync agents
            bridges = db.session.query(AgentKnowledgeBaseBridge).filter(AgentKnowledgeBaseBridge.kb_id == kb_id).all()
            for bridge in bridges:
                sync_agent_kb(bridge.agent_id)

            return kb_entry
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating text KB: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/bind", status_code=status.HTTP_200_OK, openapi_extra={"security": [{"BearerAuth": []}]})
async def bind_knowledge_base(
    request: KnowledgeBaseBind,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            # Verify agent ownership
            agent = db.session.query(AgentModel).filter(
                AgentModel.id == request.agent_id,
                AgentModel.user_id == current_user.id
            ).first()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            
            # Verify KB ownership
            kb_entry = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id == request.kb_id,
                KnowledgeBaseModel.user_id == current_user.id
            ).first()
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Knowledge base item not found")
            
            # Check if already bound
            existing_bridge = db.session.query(AgentKnowledgeBaseBridge).filter(
                AgentKnowledgeBaseBridge.agent_id == request.agent_id,
                AgentKnowledgeBaseBridge.kb_id == request.kb_id
            ).first()
            
            if existing_bridge:
                return {"message": "Knowledge base already bound to agent"}
            
            # Create bridge entry
            bridge = AgentKnowledgeBaseBridge(
                agent_id=request.agent_id,
                kb_id=request.kb_id
            )
            db.session.add(bridge)
            db.session.commit()
            
            # Sync ElevenLabs
            sync_agent_kb(request.agent_id)

            return {"message": "Knowledge base bound successfully"}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error binding knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/unbind", status_code=status.HTTP_200_OK, openapi_extra={"security": [{"BearerAuth": []}]})
async def unbind_knowledge_base(
    request: KnowledgeBaseBind,
    current_user: UnifiedAuthModel = Depends(get_current_user)
):
    try:
        with db():
            # Verify agent ownership
            agent = db.session.query(AgentModel).filter(
                AgentModel.id == request.agent_id,
                AgentModel.user_id == current_user.id
            ).first()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            
            # Find bridge entry
            bridge = db.session.query(AgentKnowledgeBaseBridge).filter(
                AgentKnowledgeBaseBridge.agent_id == request.agent_id,
                AgentKnowledgeBaseBridge.kb_id == request.kb_id
            ).first()
            
            if not bridge:
                raise HTTPException(status_code=404, detail="Binding not found")
            
            db.session.delete(bridge)
            db.session.commit()
            
            # Sync ElevenLabs
            sync_agent_kb(request.agent_id)

            return {"message": "Knowledge base unbound successfully"}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error unbinding knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
