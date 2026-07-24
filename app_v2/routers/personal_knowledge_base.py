"""
New personal knowledge base flow: files/urls/text are chunked, embedded with a
local Hugging Face sentence-transformers model, and stored in a per-user FAISS
index (chunk text/metadata in Postgres) — independent of the ElevenLabs-backed
/api/v2/knowledge-base flow.

Also exposes the RAG query used by agents: `/query` for authenticated direct
use, and `/tool-search/{user_id}` as the webhook the auto-provisioned
`search_personal_knowledge_base` agent tool calls during a live conversation
(see app_v2/utils/personal_kb_tool.py) — that route is unauthenticated by
user JWT since ElevenLabs calls it server-to-server, and is instead guarded by
a shared secret header.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File
from fastapi_sqlalchemy import db
from sqlalchemy import func
from typing import List, Optional
import math
import os
import shutil
from datetime import datetime, timezone

from app_v2.databases.models import (
    PersonalKnowledgeBaseModel,
    PersonalKnowledgeBaseChunkModel,
    UnifiedAuthModel,
)
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.personal_knowledge_base_schema import (
    PersonalKnowledgeBaseResponse,
    PersonalKnowledgeBaseURLCreate,
    PersonalKnowledgeBaseTextCreate,
    PersonalKnowledgeBaseQueryRequest,
    PersonalKnowledgeBaseQueryResult,
    PersonalKnowledgeBaseQueryResponse,
    ToolSearchRequest,
)
from app_v2.utils.jwt_utils import require_active_user
from app_v2.utils.feature_access import RequireFeature
from app_v2.utils.text_extraction import extract_text_from_file
from app_v2.utils.chunking_utils import chunk_text
from app_v2.utils.embedding_utils import generate_embeddings, generate_embedding
from app_v2.utils.faiss_store import add_embeddings, remove_embeddings, search_index
from app_v2.utils.web_scraper import scrape_url
from app_v2.utils.personal_kb_tool import ensure_personal_kb_tool, remove_personal_kb_tool_if_empty
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/personal-knowledge-base",
    tags=["Personal Knowledge Base"],
)

UPLOAD_DIR = "uploads/personal_kb"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

MAX_FILE_SIZE_IN_MB = 20
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt"}


def _store_kb_entry(
    user_id: int,
    kb_type: str,
    title: str,
    text: str,
    content_path: str = None,
    file_size: float = None,
) -> PersonalKnowledgeBaseModel:
    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="No content to embed.")

    embeddings = generate_embeddings(chunks)

    kb_entry = PersonalKnowledgeBaseModel(
        user_id=user_id,
        kb_type=kb_type,
        title=title,
        content_path=content_path,
        content_text=text,
        file_size=file_size,
    )
    db.session.add(kb_entry)
    db.session.flush()  # assign kb_entry.id without committing yet

    chunk_rows = []
    for index, chunk in enumerate(chunks):
        row = PersonalKnowledgeBaseChunkModel(kb_id=kb_entry.id, chunk_index=index, content=chunk)
        db.session.add(row)
        chunk_rows.append(row)
    db.session.flush()  # assign ids to chunk_rows, needed as FAISS vector ids

    try:
        add_embeddings(user_id, ids=[row.id for row in chunk_rows], embeddings=embeddings)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to persist embeddings to FAISS index for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to store embeddings.")

    db.session.commit()
    db.session.refresh(kb_entry)
    return kb_entry


def _kb_to_read(item: PersonalKnowledgeBaseModel) -> PersonalKnowledgeBaseResponse:
    num_chunks = (
        db.session.query(func.count(PersonalKnowledgeBaseChunkModel.id))
        .filter(PersonalKnowledgeBaseChunkModel.kb_id == item.id)
        .scalar()
        or 0
    )
    return PersonalKnowledgeBaseResponse(
        id=item.id,
        kb_type=item.kb_type,
        title=item.title,
        content_path=item.content_path,
        file_size=item.file_size,
        num_chunks=num_chunks,
        created_at=item.created_at,
        modified_at=item.modified_at,
    )


def _search_personal_kb(user_id: int, query: str, top_k: int = 5) -> List[PersonalKnowledgeBaseQueryResult]:
    embedding = generate_embedding(query)
    distances, ids = search_index(user_id, embedding, top_k=top_k)
    if ids is None:
        return []

    chunk_ids = [int(i) for i in ids if i != -1]
    if not chunk_ids:
        return []

    rows = (
        db.session.query(PersonalKnowledgeBaseChunkModel, PersonalKnowledgeBaseModel.title)
        .join(PersonalKnowledgeBaseModel, PersonalKnowledgeBaseModel.id == PersonalKnowledgeBaseChunkModel.kb_id)
        .filter(PersonalKnowledgeBaseChunkModel.id.in_(chunk_ids))
        .all()
    )
    # Inner-joined lookup so stale FAISS ids (e.g. from a since-deleted chunk
    # that hasn't been cleaned up yet) are silently dropped instead of erroring.
    rows_by_id = {chunk.id: (chunk, title) for chunk, title in rows}

    results = []
    for score, chunk_id in zip(distances, chunk_ids):
        match = rows_by_id.get(chunk_id)
        if not match:
            continue
        chunk, title = match
        results.append(
            PersonalKnowledgeBaseQueryResult(
                kb_id=chunk.kb_id,
                title=title,
                content=chunk.content,
                score=float(score),
            )
        )
    return results


@router.post(
    "/file",
    response_model=List[PersonalKnowledgeBaseResponse],
    openapi_extra={"security": [{"BearerAuth": []}]},
    status_code=status.HTTP_201_CREATED,
)
async def add_files(
    files: List[UploadFile] = File(...),
    current_user: UnifiedAuthModel = Depends(RequireFeature("knowledge_base")),
):
    try:
        with db():
            uploaded_entries = []
            seen_filenames = set()

            for file in files:
                _, ext = os.path.splitext(file.filename)
                if ext.lower() not in ALLOWED_EXTENSIONS:
                    raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

                filename_key = file.filename.lower()
                if filename_key in seen_filenames:
                    raise HTTPException(status_code=400, detail=f"Duplicate file name '{file.filename}' in this upload request.")
                seen_filenames.add(filename_key)

                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
                file_size_mb = file_size / (1024 * 1024)

                if file_size == 0:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")
                if file_size_mb > MAX_FILE_SIZE_IN_MB:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds system 20MB hard limit.")

                file_path = os.path.join(UPLOAD_DIR, f"{current_user.id}_{datetime.now(timezone.utc).timestamp()}_{file.filename}")
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)

                try:
                    text = extract_text_from_file(file_path)
                    kb_entry = _store_kb_entry(
                        user_id=current_user.id,
                        kb_type="file",
                        title=file.filename,
                        text=text,
                        content_path=file_path,
                        file_size=round(file_size / 1024, 2),  # stored in KB
                    )
                except HTTPException:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    raise
                except Exception as e:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    logger.error(f"Error processing file '{file.filename}' for personal KB: {e}")
                    raise HTTPException(status_code=422, detail=f"Could not process file {file.filename}")

                uploaded_entries.append(kb_entry)

            logger.info(f"{len(uploaded_entries)} files added to personal KB for user: {current_user.email}")
            result = [_kb_to_read(entry) for entry in uploaded_entries]

        ensure_personal_kb_tool(current_user.id)
        return result

    except HTTPException as e:
        logger.error(f"HTTP Exception during personal KB file upload: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during personal KB file upload: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/url",
    response_model=PersonalKnowledgeBaseResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
    status_code=status.HTTP_201_CREATED,
)
async def add_url(request: PersonalKnowledgeBaseURLCreate, current_user: UnifiedAuthModel = Depends(RequireFeature("knowledge_base"))):
    try:
        url_str = str(request.url)
        with db():
            existing_url = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.user_id == current_user.id,
                PersonalKnowledgeBaseModel.kb_type == "url",
                func.lower(PersonalKnowledgeBaseModel.content_path) == url_str.lower(),
            ).first()
            if existing_url:
                raise HTTPException(status_code=400, detail="This URL has already been added to your knowledge base.")

            title, text = scrape_url(url_str)

            kb_entry = _store_kb_entry(
                user_id=current_user.id,
                kb_type="url",
                title=title,
                text=text,
                content_path=url_str,
            )

            logger.info(f"URL added to personal KB for user: {current_user.email}")
            result = _kb_to_read(kb_entry)

        ensure_personal_kb_tool(current_user.id)
        return result

    except HTTPException as e:
        logger.error(f"HTTP Exception during personal KB URL addition: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during personal KB URL addition: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/text",
    response_model=PersonalKnowledgeBaseResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
    status_code=status.HTTP_201_CREATED,
)
async def add_text(request: PersonalKnowledgeBaseTextCreate, current_user: UnifiedAuthModel = Depends(RequireFeature("knowledge_base"))):
    try:
        with db():
            existing_text = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.user_id == current_user.id,
                PersonalKnowledgeBaseModel.kb_type == "text",
                PersonalKnowledgeBaseModel.title == request.title,
            ).first()
            if existing_text:
                raise HTTPException(status_code=400, detail="This exact text content has already been added to your knowledge base.")

            kb_entry = _store_kb_entry(
                user_id=current_user.id,
                kb_type="text",
                title=request.title,
                text=request.content,
            )

            logger.info(f"Text added to personal KB for user: {current_user.email}")
            result = _kb_to_read(kb_entry)

        ensure_personal_kb_tool(current_user.id)
        return result

    except HTTPException as e:
        logger.error(f"HTTP Exception during personal KB text addition: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during personal KB text addition: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/",
    response_model=PaginatedResponse[PersonalKnowledgeBaseResponse],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_all_personal_kb(
    page: int = 1,
    size: int = 20,
    title: Optional[str] = None,
    kb_type: Optional[str] = None,
    sort_by: Optional[str] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """sort_by: date_added_asc | date_added_desc — anything else (including
    absent) falls back to the default "last updated" order."""
    try:
        if page < 1:
            page = 1
        skip = (page - 1) * size

        with db():
            query = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.user_id == current_user.id
            )

            if title:
                query = query.filter(PersonalKnowledgeBaseModel.title.ilike(f"%{title}%"))
            if kb_type:
                query = query.filter(PersonalKnowledgeBaseModel.kb_type == kb_type)

            if sort_by == "date_added_asc":
                query = query.order_by(PersonalKnowledgeBaseModel.created_at.asc())
            elif sort_by == "date_added_desc":
                query = query.order_by(PersonalKnowledgeBaseModel.created_at.desc())
            else:
                query = query.order_by(PersonalKnowledgeBaseModel.modified_at.desc())

            total = query.count()
            pages = math.ceil(total / size) if size > 0 else 1
            entries = query.offset(skip).limit(size).all()

            items = [_kb_to_read(entry) for entry in entries]

            return PaginatedResponse(total=total, page=page, size=size, pages=pages, items=items)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error retrieving personal knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT, openapi_extra={"security": [{"BearerAuth": []}]})
async def delete_personal_kb_item(
    kb_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    try:
        with db():
            kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id == kb_id,
                PersonalKnowledgeBaseModel.user_id == current_user.id,
            ).first()
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Knowledge base item not found")

            chunk_ids = [
                row.id for row in db.session.query(PersonalKnowledgeBaseChunkModel.id)
                .filter(PersonalKnowledgeBaseChunkModel.kb_id == kb_entry.id).all()
            ]

            if kb_entry.kb_type == "file" and kb_entry.content_path and os.path.exists(kb_entry.content_path):
                try:
                    os.remove(kb_entry.content_path)
                except OSError as e:
                    logger.warning(f"Failed to delete file {kb_entry.content_path}: {e}")

            db.session.delete(kb_entry)  # cascades chunk rows
            db.session.commit()

        try:
            remove_embeddings(current_user.id, chunk_ids)
        except Exception as e:
            logger.warning(f"Failed to remove FAISS vectors for deleted KB item {kb_id}: {e}")

        remove_personal_kb_tool_if_empty(current_user.id)
        logger.info(f"Deleted personal KB item {kb_id} for user {current_user.email}")
        return

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error deleting personal knowledge base item: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/query",
    response_model=PersonalKnowledgeBaseQueryResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def query_personal_kb(
    request: PersonalKnowledgeBaseQueryRequest,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    try:
        with db():
            results = _search_personal_kb(current_user.id, request.query, top_k=request.top_k)
            return PersonalKnowledgeBaseQueryResponse(results=results)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error querying personal knowledge base: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/tool-search/{user_id}", include_in_schema=False)
async def tool_search_webhook(user_id: int, request: ToolSearchRequest, http_request: Request):
    """
    Webhook target for the auto-provisioned `search_personal_knowledge_base`
    agent tool (see app_v2/utils/personal_kb_tool.py). Called directly by
    ElevenLabs during a live conversation — no user JWT, guarded by a shared
    secret header instead.
    """
    expected_secret = VoiceSettings.PERSONAL_KB_TOOL_SECRET
    provided_secret = http_request.headers.get("x-api-key")
    if not expected_secret or provided_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with db():
            results = _search_personal_kb(user_id, request.query, top_k=5)
            return {"results": [r.model_dump() for r in results]}
    except Exception as e:
        logger.error(f"Error in personal KB tool-search webhook for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
