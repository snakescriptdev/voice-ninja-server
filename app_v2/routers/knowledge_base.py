from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from fastapi_sqlalchemy import db
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Optional
import os
import shutil
import logging
import mimetypes
from datetime import datetime, timezone
from app_v2.schemas.pagination import PaginatedResponse
import math

from app_v2.databases.models import KnowledgeBaseModel, AgentModel, UnifiedAuthModel, AgentKnowledgeBaseBridge
from app_v2.schemas.knowledge_base_schema import (
    KnowledgeBaseResponse,
    KnowledgeBaseURLCreate,
    KnowledgeBaseTextCreate,
    KnowledgeBaseURLUpdate,
    KnowledgeBaseTextUpdate,
    KnowledgeBaseBind
)
from app_v2.utils.jwt_utils import HTTPBearer,require_active_user
from app_v2.utils.feature_access import RequireFeature, get_feature_limit, get_feature_usage
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

MAX_FILE_SIZE_IN_MB = 20 
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
    current_user: UnifiedAuthModel = Depends(RequireFeature("knowledge_base"))
):
    try:
        user_id = current_user.id
        limit = get_feature_limit(user_id, "knowledge_base") # in MB
        current_usage = get_feature_usage(user_id, "knowledge_base") # in MB
        
        with db():
            uploaded_entries = []
            # Plan-based limit per file (in MB)
            plan_limit_mb = limit if limit is not None else MAX_FILE_SIZE_IN_MB # Fallback to 10MB if no limit set

            seen_filenames = set()
            for file in files:
                # validation logic
                _, ext = os.path.splitext(file.filename)
                if ext.lower() not in ALLOWED_EXTENSIONS:
                    raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

                filename_key = file.filename.lower()
                if filename_key in seen_filenames:
                    raise HTTPException(status_code=400, detail=f"Duplicate file name '{file.filename}' in this upload request.")
                seen_filenames.add(filename_key)

                existing_file = db.session.query(KnowledgeBaseModel).filter(
                    KnowledgeBaseModel.user_id == current_user.id,
                    KnowledgeBaseModel.kb_type == "file",
                    func.lower(KnowledgeBaseModel.title) == filename_key
                ).first()
                if existing_file:
                    raise HTTPException(
                        status_code=400,
                        detail=f"A file named '{file.filename}' already exists in your knowledge base."
                    )

                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
                
                file_size_mb = file_size / (1024 * 1024)

                # enforce plan limit and hard cap
                if plan_limit_mb is not None and file_size_mb > plan_limit_mb:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Your current plan does not support files larger than {plan_limit_mb}MB."
                    )

                if file_size_mb > MAX_FILE_SIZE_IN_MB:
                     raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds system 20MB hard limit.")
                
                if file_size == 0:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")

                file_path = os.path.join(UPLOAD_DIR, f"{current_user.id}_{datetime.now(timezone.utc).timestamp()}_{file.filename}")
                
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
                    file_size=round((file_size /(1024)),2)    #file size in kb
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
async def add_url(request: KnowledgeBaseURLCreate, current_user: UnifiedAuthModel = Depends(RequireFeature("knowledge_base"))):
    try:
        url_str = str(request.url)
        with db():
            existing_url = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "url",
                func.lower(KnowledgeBaseModel.content_path) == url_str.lower()
            ).first()
            if existing_url:
                raise HTTPException(
                    status_code=400,
                    detail="This URL has already been added to your knowledge base."
                )

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
async def add_text(request: KnowledgeBaseTextCreate, current_user: UnifiedAuthModel = Depends(RequireFeature("knowledge_base"))):
    try:
        with db():
            existing_text = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "text",
                KnowledgeBaseModel.title == request.title
            ).first()
            if existing_text:
                raise HTTPException(
                    status_code=400,
                    detail="This exact text content has already been added to your knowledge base."
                )

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
    current_user: UnifiedAuthModel = Depends(require_active_user())
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
                .order_by(KnowledgeBaseModel.modified_at.desc())
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
    current_user: UnifiedAuthModel = Depends(require_active_user())
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
                .order_by(KnowledgeBaseModel.modified_at.desc())
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

@router.get("/{kb_id}/file", openapi_extra={"security": [{"BearerAuth": []}]})
async def download_knowledge_base_file(
    kb_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user())
):
    """Streams the raw file for a file-type KB item so the user can view/download it."""
    try:
        with db():
            kb_entry = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id == kb_id,
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "file"
            ).first()

            if not kb_entry:
                raise HTTPException(status_code=404, detail="File Knowledge base item not found")

            if not kb_entry.content_path or not os.path.exists(kb_entry.content_path):
                raise HTTPException(status_code=404, detail="File not found on server")

            filename = kb_entry.title or os.path.basename(kb_entry.content_path)
            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

            return FileResponse(
                path=kb_entry.content_path,
                media_type=media_type,
                filename=filename,
            )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error downloading knowledge base file: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT, openapi_extra={"security": [{"BearerAuth": []}]})
async def delete_knowledge_base_item(
    kb_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user())
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
    title: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    current_user: UnifiedAuthModel = Depends(require_active_user())
):
    """
    Renames the KB item and/or replaces its underlying file. Passing a new
    `file` always triggers a re-sync with ElevenLabs — even if it has the
    same filename as the current one — since the content may differ.
    """
    try:
        with db():
            kb_entry = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id == kb_id,
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "file"
            ).first()

            if not kb_entry:
                raise HTTPException(status_code=404, detail="File Knowledge base item not found")

            new_file_path = None
            new_file_size_mb = None
            new_title = title if title is not None else kb_entry.title

            if file is not None:
                _, ext = os.path.splitext(file.filename)
                if ext.lower() not in ALLOWED_EXTENSIONS:
                    raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
                if file_size == 0:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")

                new_file_size_mb = file_size / (1024 * 1024)
                limit = get_feature_limit(current_user.id, "knowledge_base")
                plan_limit_mb = limit if limit is not None else MAX_FILE_SIZE_IN_MB
                if plan_limit_mb is not None and new_file_size_mb > plan_limit_mb:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Your current plan does not support files larger than {plan_limit_mb}MB."
                    )
                if new_file_size_mb > MAX_FILE_SIZE_IN_MB:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds system 20MB hard limit.")

                # Exclude this item itself — replacing a file with another
                # copy of the same name is exactly what this endpoint is for.
                existing_file = db.session.query(KnowledgeBaseModel).filter(
                    KnowledgeBaseModel.user_id == current_user.id,
                    KnowledgeBaseModel.kb_type == "file",
                    KnowledgeBaseModel.id != kb_id,
                    func.lower(KnowledgeBaseModel.title) == file.filename.lower()
                ).first()
                if existing_file:
                    raise HTTPException(
                        status_code=400,
                        detail=f"A file named '{file.filename}' already exists in your knowledge base."
                    )

                new_file_path = os.path.join(UPLOAD_DIR, f"{current_user.id}_{datetime.now(timezone.utc).timestamp()}_{file.filename}")
                with open(new_file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)

                if title is None:
                    new_title = file.filename

            changed = new_file_path is not None or new_title != kb_entry.title

            if changed:
                # ElevenLabs doesn't support in-place renaming/content updates
                # for every KB type, so edits are applied by creating a new
                # document first and only deleting the old one once that
                # succeeds — deleting first would leave elevenlabs_document_id
                # pointing at a document that no longer exists if the
                # re-create then fails, which poisons every future agent sync
                # referencing this KB (ElevenLabs rejects the whole
                # knowledge_base array over one stale id).
                kb_client = ElevenLabsKB()
                old_document_id = kb_entry.elevenlabs_document_id
                old_file_path = kb_entry.content_path

                upload_path = new_file_path or kb_entry.content_path
                if not upload_path or not os.path.exists(upload_path):
                    if new_file_path and os.path.exists(new_file_path):
                        os.remove(new_file_path)
                    raise HTTPException(status_code=400, detail="Local file missing, cannot re-sync with ElevenLabs")

                kb_response = kb_client.upload_document(upload_path, name=new_title)
                if not kb_response.status:
                    if new_file_path and os.path.exists(new_file_path):
                        os.remove(new_file_path)
                    logger.error(f"Failed to re-sync file KB: {kb_response.error_message}")
                    db.session.rollback()
                    raise HTTPException(
                        status_code=424,
                        detail=f"Failed to sync file with ElevenLabs: {kb_response.error_message}"
                    )

                kb_entry.title = new_title
                kb_entry.elevenlabs_document_id = kb_response.data.get("document_id")
                kb_entry.rag_index_id = kb_client.compute_rag_index(kb_entry.elevenlabs_document_id)

                if new_file_path:
                    kb_entry.content_path = new_file_path
                    kb_entry.file_size = round(new_file_size_mb * 1024, 2)  # stored in KB
                    if old_file_path and old_file_path != new_file_path and os.path.exists(old_file_path):
                        try:
                            os.remove(old_file_path)
                        except OSError as e:
                            logger.warning(f"Failed to remove old file {old_file_path}: {e}")

                if old_document_id:
                    kb_client.delete_document(old_document_id)

            db.session.commit()
            db.session.refresh(kb_entry)

            # Sync agents this KB is attached to, so they immediately pick up
            # the new document/RAG index.
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
    current_user: UnifiedAuthModel = Depends(require_active_user())
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

            title_changed = update_data.title is not None and update_data.title != kb_entry.title
            url_changed = update_data.url is not None and str(update_data.url) != kb_entry.content_path

            if title_changed:
                kb_entry.title = update_data.title
            if url_changed:
                kb_entry.content_path = str(update_data.url)

            if title_changed or url_changed:
                # Create the new document first, delete the old one only on
                # success — see comment in update_file_knowledge_base for why
                # delete-then-create is unsafe.
                kb_client = ElevenLabsKB()
                old_document_id = kb_entry.elevenlabs_document_id

                kb_response = kb_client.add_url_document(kb_entry.content_path, name=kb_entry.title)
                if not kb_response.status:
                    logger.error(f"Failed to re-sync URL KB: {kb_response.error_message}")
                    db.session.rollback()
                    raise HTTPException(
                        status_code=424,
                        detail=f"Failed to sync URL with ElevenLabs: {kb_response.error_message}"
                    )

                kb_entry.elevenlabs_document_id = kb_response.data.get("document_id")
                # Compute new RAG index
                kb_entry.rag_index_id = kb_client.compute_rag_index(kb_entry.elevenlabs_document_id)
                if old_document_id:
                    kb_client.delete_document(old_document_id)

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
    current_user: UnifiedAuthModel = Depends(require_active_user())
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

            title_changed = update_data.title is not None and update_data.title != kb_entry.title
            content_changed = update_data.content_text is not None and update_data.content_text != kb_entry.content_text

            if title_changed:
                kb_entry.title = update_data.title
            if content_changed:
                kb_entry.content_text = update_data.content_text

            if title_changed or content_changed:
                # Create the new document first, delete the old one only on
                # success — see comment in update_file_knowledge_base for why
                # delete-then-create is unsafe.
                kb_client = ElevenLabsKB()
                old_document_id = kb_entry.elevenlabs_document_id

                kb_response = kb_client.add_text_document(kb_entry.content_text, name=kb_entry.title)
                if not kb_response.status:
                    logger.error(f"Failed to re-sync text KB: {kb_response.error_message}")
                    db.session.rollback()
                    raise HTTPException(
                        status_code=424,
                        detail=f"Failed to sync text with ElevenLabs: {kb_response.error_message}"
                    )

                kb_entry.elevenlabs_document_id = kb_response.data.get("document_id")
                # Compute new RAG index
                kb_entry.rag_index_id = kb_client.compute_rag_index(kb_entry.elevenlabs_document_id)
                if old_document_id:
                    kb_client.delete_document(old_document_id)

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
    current_user: UnifiedAuthModel = Depends(require_active_user())
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
    current_user: UnifiedAuthModel = Depends(require_active_user())
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
