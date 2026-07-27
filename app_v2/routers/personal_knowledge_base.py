"""
New personal knowledge base flow: files/urls/text are chunked, embedded with a
local Hugging Face sentence-transformers model, and stored in a per-user FAISS
index (chunk text/metadata in Postgres) — independent of the ElevenLabs-backed
/api/v2/knowledge-base flow.

KB items are attached to agents many-to-many via
PersonalKnowledgeBaseAgentBridgeModel; the search_personal_knowledge_base
tool/prompt only appears on an agent once it has at least one KB item
attached (see app_v2/utils/personal_kb_tool.py), and search is scoped to
that agent's attached items only.

Also exposes the RAG query used by agents: `/query` for authenticated direct
use (searches all of the user's KB items), and `/tool-search/{agent_id}` as
the webhook the auto-provisioned `search_personal_knowledge_base` agent tool
calls during a live conversation — that route is unauthenticated by user JWT
since ElevenLabs calls it server-to-server, and is instead guarded by a
shared secret header.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi_sqlalchemy import db
from sqlalchemy import func
from typing import List, Optional
import math
import mimetypes
import os
import secrets
import shutil
from datetime import datetime, timezone

from app_v2.databases.models import (
    PersonalKnowledgeBaseModel,
    PersonalKnowledgeBaseChunkModel,
    PersonalKnowledgeBaseAgentBridgeModel,
    AgentModel,
    UnifiedAuthModel,
)
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.personal_knowledge_base_schema import (
    PersonalKnowledgeBaseResponse,
    PersonalKnowledgeBaseURLCreate,
    PersonalKnowledgeBaseTextCreate,
    PersonalKnowledgeBaseURLUpdate,
    PersonalKnowledgeBaseTextUpdate,
    PersonalKnowledgeBaseQueryRequest,
    PersonalKnowledgeBaseQueryResult,
    PersonalKnowledgeBaseQueryResponse,
    PersonalKnowledgeBaseAnswerResponse,
    ToolSearchRequest,
)
from app_v2.utils.jwt_utils import require_active_user
from app_v2.utils.feature_access import RequireFeature
from app_v2.utils.text_extraction import extract_text_from_file
from app_v2.utils.chunking_utils import chunk_text
from app_v2.utils.faiss_store import add_embeddings, remove_embeddings, search_index
from app_v2.utils.web_scraper import scrape_url
from app_v2.utils.personal_kb_tool import ensure_personal_kb_tool_for_agent, remove_personal_kb_tool_from_agent_if_empty
from app_v2.utils.personal_kb_answer import generate_kb_answer
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


def _require_internal_auth(http_request: Request) -> None:
    """
    Guards internal server-to-server webhooks (e.g. the tool-search webhook
    below) that ElevenLabs calls directly with no user JWT. Requires
    `Authorization: Bearer <INTERNAL_API_SECRET_KEY>`; rejects if the key
    isn't configured, the header is missing/malformed, or it doesn't match —
    using a constant-time comparison so response timing can't leak how much
    of the key was guessed correctly.
    """
    expected_secret = VoiceSettings.INTERNAL_API_SECRET_KEY
    if not expected_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    auth_header = http_request.headers.get("authorization") or ""
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not secrets.compare_digest(token, expected_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _store_kb_entry(
    user_id: int,
    kb_type: str,
    title: str,
    text: str,
    content_path: str = None,
    file_size: float = None,
    embed_text: str = None,
) -> PersonalKnowledgeBaseModel:
    """
    `embed_text` is what gets chunked/embedded/stored as chunk content, and
    defaults to `text`. Callers pass it explicitly when the searchable text
    should include more than just `text` (e.g. text-type entries, where the
    title carries as much meaning as the content) — `text` alone still ends
    up in `content_text` for display/editing.
    """
    chunks = chunk_text(embed_text if embed_text is not None else text)
    if not chunks:
        raise HTTPException(status_code=400, detail="No content to embed.")

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
        add_embeddings(
            user_id,
            ids=[row.id for row in chunk_rows],
            texts=chunks,
            kb_ids=[kb_entry.id] * len(chunks),
        )
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
        content_text=item.content_text,
        file_size=item.file_size,
        num_chunks=num_chunks,
        created_at=item.created_at,
        modified_at=item.modified_at,
    )


def _replace_kb_content(kb_entry: PersonalKnowledgeBaseModel, new_text: str, embed_text: str = None) -> None:
    """
    Re-chunk and re-embed a KB item's content in place. Writes the new chunks
    and FAISS vectors first and only removes the old ones once the new ones
    are safely persisted, so a mid-update failure never leaves the item
    without embeddings. Does not commit — caller owns the transaction.

    `embed_text` overrides what gets chunked/embedded (defaults to `new_text`)
    — pass it when the searchable text should include more than the raw
    content, e.g. a text-type entry's title alongside its content.
    """
    chunks = chunk_text(embed_text if embed_text is not None else new_text)
    if not chunks:
        raise HTTPException(status_code=400, detail="No content to embed.")

    old_chunk_ids = [
        row.id for row in db.session.query(PersonalKnowledgeBaseChunkModel.id)
        .filter(PersonalKnowledgeBaseChunkModel.kb_id == kb_entry.id).all()
    ]

    new_rows = []
    for index, chunk in enumerate(chunks):
        row = PersonalKnowledgeBaseChunkModel(kb_id=kb_entry.id, chunk_index=index, content=chunk)
        db.session.add(row)
        new_rows.append(row)
    db.session.flush()

    try:
        add_embeddings(
            kb_entry.user_id,
            ids=[row.id for row in new_rows],
            texts=chunks,
            kb_ids=[kb_entry.id] * len(chunks),
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to persist updated embeddings for kb {kb_entry.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to store embeddings.")

    if old_chunk_ids:
        db.session.query(PersonalKnowledgeBaseChunkModel).filter(
            PersonalKnowledgeBaseChunkModel.id.in_(old_chunk_ids)
        ).delete(synchronize_session=False)
        db.session.flush()

    kb_entry.content_text = new_text

    if old_chunk_ids:
        try:
            remove_embeddings(kb_entry.user_id, old_chunk_ids)
        except Exception as e:
            logger.warning(f"Failed to remove stale FAISS vectors for kb {kb_entry.id}: {e}")


def _search_personal_kb(user_id: int, query: str, top_k: int = 5) -> List[PersonalKnowledgeBaseQueryResult]:
    matches = search_index(user_id, query, top_k=top_k)
    if not matches:
        return []

    chunk_ids = [int(chunk_id) for chunk_id, _ in matches]
    scores_by_chunk_id = {int(chunk_id): score for chunk_id, score in matches}

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
    for chunk_id in chunk_ids:
        match = rows_by_id.get(chunk_id)
        if not match:
            continue
        chunk, title = match
        results.append(
            PersonalKnowledgeBaseQueryResult(
                kb_id=chunk.kb_id,
                title=title,
                content=chunk.content,
                score=scores_by_chunk_id[chunk_id],
            )
        )
    return results


def _search_personal_kb_for_agent(agent_id: int, user_id: int, query: str, top_k: int = 5) -> List[PersonalKnowledgeBaseQueryResult]:
    """
    Like `_search_personal_kb`, but scoped to only the KB items attached to
    `agent_id`. Each vector in the user's FAISS store carries its owning
    kb_id in metadata, so the allowed kb_ids are passed straight into
    `search_index` and filtered inside FAISS itself before ranking/
    truncating to `top_k` — not approximated by over-fetching and joining
    in Postgres afterward.
    """
    allowed_kb_ids = [
        row.kb_id for row in db.session.query(PersonalKnowledgeBaseAgentBridgeModel.kb_id)
        .filter(PersonalKnowledgeBaseAgentBridgeModel.agent_id == agent_id).all()
    ]
    if not allowed_kb_ids:
        return []

    matches = search_index(user_id, query, top_k=top_k, kb_ids=allowed_kb_ids)
    if not matches:
        return []

    chunk_ids = [int(chunk_id) for chunk_id, _ in matches]
    scores_by_chunk_id = {int(chunk_id): score for chunk_id, score in matches}

    rows = (
        db.session.query(PersonalKnowledgeBaseChunkModel, PersonalKnowledgeBaseModel.title)
        .join(PersonalKnowledgeBaseModel, PersonalKnowledgeBaseModel.id == PersonalKnowledgeBaseChunkModel.kb_id)
        .filter(PersonalKnowledgeBaseChunkModel.id.in_(chunk_ids))
        .all()
    )
    # Inner-joined lookup so a stale FAISS chunk_id (e.g. from a since-deleted
    # chunk that hasn't been cleaned up yet) is silently dropped instead of erroring.
    rows_by_id = {chunk.id: (chunk, title) for chunk, title in rows}

    results = []
    for chunk_id in chunk_ids:  # already ranked best-first by search_index
        match = rows_by_id.get(chunk_id)
        if not match:
            continue
        chunk, title = match
        results.append(
            PersonalKnowledgeBaseQueryResult(
                kb_id=chunk.kb_id,
                title=title,
                content=chunk.content,
                score=scores_by_chunk_id[chunk_id],
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
                embed_text=f"{request.title}\n\n{request.content}",
            )

            logger.info(f"Text added to personal KB for user: {current_user.email}")
            result = _kb_to_read(kb_entry)

        return result

    except HTTPException as e:
        logger.error(f"HTTP Exception during personal KB text addition: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during personal KB text addition: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/{kb_id}/url",
    response_model=PersonalKnowledgeBaseResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_personal_kb_url(
    kb_id: int,
    request: PersonalKnowledgeBaseURLUpdate,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Edit a URL-type KB item — re-scraping and re-embedding its content if
    the URL changed. Old chunks/embeddings are removed once the new ones are
    safely persisted (see _replace_kb_content)."""
    try:
        with db():
            kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id == kb_id,
                PersonalKnowledgeBaseModel.user_id == current_user.id,
                PersonalKnowledgeBaseModel.kb_type == "url",
            ).first()
            if not kb_entry:
                raise HTTPException(status_code=404, detail="URL Knowledge Base item not found")

            if request.url is not None:
                new_url = str(request.url)
                title, text = scrape_url(new_url)
                _replace_kb_content(kb_entry, text)
                kb_entry.content_path = new_url
                kb_entry.title = request.title if request.title is not None else title
            elif request.title is not None:
                kb_entry.title = request.title

            db.session.commit()
            db.session.refresh(kb_entry)
            return _kb_to_read(kb_entry)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating personal KB URL item {kb_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/{kb_id}/text",
    response_model=PersonalKnowledgeBaseResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_personal_kb_text(
    kb_id: int,
    request: PersonalKnowledgeBaseTextUpdate,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Edit a text-type KB item — re-chunking and re-embedding its content
    (title + content combined, same as on creation) if the content changed."""
    try:
        with db():
            kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id == kb_id,
                PersonalKnowledgeBaseModel.user_id == current_user.id,
                PersonalKnowledgeBaseModel.kb_type == "text",
            ).first()
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Text Knowledge Base item not found")

            if request.content is not None:
                new_title = request.title if request.title is not None else kb_entry.title
                _replace_kb_content(kb_entry, request.content, embed_text=f"{new_title}\n\n{request.content}")
            if request.title is not None:
                kb_entry.title = request.title

            db.session.commit()
            db.session.refresh(kb_entry)
            return _kb_to_read(kb_entry)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating personal KB text item {kb_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/{kb_id}/file",
    response_model=PersonalKnowledgeBaseResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_personal_kb_file(
    kb_id: int,
    title: str = Form(None),
    file: UploadFile = File(None),
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Edit a file-type KB item — replacing the uploaded file (and
    re-extracting/re-embedding its text) if a new one is provided."""
    try:
        with db():
            kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id == kb_id,
                PersonalKnowledgeBaseModel.user_id == current_user.id,
                PersonalKnowledgeBaseModel.kb_type == "file",
            ).first()
            if not kb_entry:
                raise HTTPException(status_code=404, detail="File Knowledge Base item not found")

            if file is not None:
                _, ext = os.path.splitext(file.filename)
                if ext.lower() not in ALLOWED_EXTENSIONS:
                    raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
                if file_size == 0:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")
                if file_size > MAX_FILE_SIZE_IN_MB * 1024 * 1024:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds {MAX_FILE_SIZE_IN_MB}MB limit")

                new_file_path = os.path.join(UPLOAD_DIR, f"{current_user.id}_{datetime.now(timezone.utc).timestamp()}_{file.filename}")
                with open(new_file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)

                try:
                    text = extract_text_from_file(new_file_path)
                    _replace_kb_content(kb_entry, text)
                except HTTPException:
                    if os.path.exists(new_file_path):
                        os.remove(new_file_path)
                    raise
                except Exception as e:
                    if os.path.exists(new_file_path):
                        os.remove(new_file_path)
                    logger.error(f"Error processing file update for personal kb {kb_id}: {e}")
                    raise HTTPException(status_code=422, detail="Could not process file")

                old_path = kb_entry.content_path
                kb_entry.content_path = new_file_path
                kb_entry.file_size = round(file_size / 1024, 2)
                kb_entry.title = title if title is not None else file.filename
                if old_path and os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
            elif title is not None:
                kb_entry.title = title

            db.session.commit()
            db.session.refresh(kb_entry)
            return _kb_to_read(kb_entry)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating personal KB file item {kb_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{kb_id}/file", openapi_extra={"security": [{"BearerAuth": []}]})
async def download_personal_kb_file(
    kb_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Streams the raw file for a file-type KB item so the user can view/download it."""
    try:
        with db():
            kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id == kb_id,
                PersonalKnowledgeBaseModel.user_id == current_user.id,
                PersonalKnowledgeBaseModel.kb_type == "file",
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
        logger.error(f"Error downloading personal KB file {kb_id}: {str(e)}")
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


@router.get(
    "/agents/{agent_id}",
    response_model=List[PersonalKnowledgeBaseResponse],
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_personal_kb_for_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """List personal KB items currently attached to `agent_id` — used by the
    agent create/edit form to pre-populate its picker."""
    try:
        with db():
            agent = db.session.query(AgentModel).filter(
                AgentModel.id == agent_id,
                AgentModel.user_id == current_user.id,
            ).first()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

            entries = (
                db.session.query(PersonalKnowledgeBaseModel)
                .join(
                    PersonalKnowledgeBaseAgentBridgeModel,
                    PersonalKnowledgeBaseAgentBridgeModel.kb_id == PersonalKnowledgeBaseModel.id,
                )
                .filter(PersonalKnowledgeBaseAgentBridgeModel.agent_id == agent_id)
                .all()
            )
            return [_kb_to_read(entry) for entry in entries]

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error listing personal KB items for agent {agent_id}: {str(e)}")
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
            # Capture attached agent ids before deleting — the bridge rows
            # cascade away with kb_entry, so this is the last chance to know
            # which agents need their "any KB left?" tool check afterward.
            attached_agent_ids = [
                row.agent_id for row in db.session.query(PersonalKnowledgeBaseAgentBridgeModel.agent_id)
                .filter(PersonalKnowledgeBaseAgentBridgeModel.kb_id == kb_entry.id).all()
            ]

            if kb_entry.kb_type == "file" and kb_entry.content_path and os.path.exists(kb_entry.content_path):
                try:
                    os.remove(kb_entry.content_path)
                except OSError as e:
                    logger.warning(f"Failed to delete file {kb_entry.content_path}: {e}")

            db.session.delete(kb_entry)  # cascades chunk rows + agent bridge rows
            db.session.commit()

        try:
            remove_embeddings(current_user.id, chunk_ids)
        except Exception as e:
            logger.warning(f"Failed to remove FAISS vectors for deleted KB item {kb_id}: {e}")

        for agent_id in attached_agent_ids:
            try:
                remove_personal_kb_tool_from_agent_if_empty(agent_id)
            except Exception as e:
                logger.warning(f"Failed to sync personal KB tool removal for agent {agent_id}: {e}")

        logger.info(f"Deleted personal KB item {kb_id} for user {current_user.email}")
        return

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error deleting personal knowledge base item: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/{kb_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def attach_personal_kb_to_agent(
    kb_id: int,
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Attach a KB item to an agent — idempotent. Provisions that agent's
    own search_personal_knowledge_base tool + prompt block if this is its
    first attached KB item."""
    try:
        with db():
            kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id == kb_id,
                PersonalKnowledgeBaseModel.user_id == current_user.id,
            ).first()
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Knowledge base item not found")

            agent = db.session.query(AgentModel).filter(
                AgentModel.id == agent_id,
                AgentModel.user_id == current_user.id,
            ).first()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

            existing = db.session.query(PersonalKnowledgeBaseAgentBridgeModel).filter(
                PersonalKnowledgeBaseAgentBridgeModel.kb_id == kb_id,
                PersonalKnowledgeBaseAgentBridgeModel.agent_id == agent_id,
            ).first()
            if not existing:
                db.session.add(PersonalKnowledgeBaseAgentBridgeModel(kb_id=kb_id, agent_id=agent_id))
                db.session.commit()

        ensure_personal_kb_tool_for_agent(agent_id)
        return

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error attaching personal KB {kb_id} to agent {agent_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete(
    "/{kb_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def detach_personal_kb_from_agent(
    kb_id: int,
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """Detach a KB item from an agent. If this was the agent's last attached
    KB item, its search_personal_knowledge_base tool + prompt block are
    removed too."""
    try:
        with db():
            kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id == kb_id,
                PersonalKnowledgeBaseModel.user_id == current_user.id,
            ).first()
            if not kb_entry:
                raise HTTPException(status_code=404, detail="Knowledge base item not found")

            db.session.query(PersonalKnowledgeBaseAgentBridgeModel).filter(
                PersonalKnowledgeBaseAgentBridgeModel.kb_id == kb_id,
                PersonalKnowledgeBaseAgentBridgeModel.agent_id == agent_id,
            ).delete()
            db.session.commit()

        remove_personal_kb_tool_from_agent_if_empty(agent_id)
        return

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error detaching personal KB {kb_id} from agent {agent_id}: {str(e)}")
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


@router.post(
    "/tool-search/{agent_id}",
    response_model=PersonalKnowledgeBaseAnswerResponse,
    include_in_schema=False,
)
async def tool_search_webhook(agent_id: int, request: ToolSearchRequest, http_request: Request):
    """
    Webhook target for the auto-provisioned `search_personal_knowledge_base`
    agent tool (see app_v2/utils/personal_kb_tool.py) — one tool per agent,
    scoped to only that agent's attached KB items. Called directly by
    ElevenLabs during a live conversation — no user JWT, guarded by a shared
    secret header instead.

    Returns a ready-to-speak `answer` synthesized from the matching KB
    excerpts (via generate_kb_answer), not just the raw excerpts — the
    calling agent relays it directly instead of composing its own answer
    from raw search results. `request.conversation_context` (an LLM-authored
    summary of recent relevant turns, if the calling agent passed one) is
    folded into that synthesis so follow-up questions resolve correctly.
    """
    _require_internal_auth(http_request)

    try:
        with db():
            agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            results = _search_personal_kb_for_agent(agent_id, agent.user_id, request.query, top_k=5)

        answer = await generate_kb_answer(request.query, results, request.conversation_context)
        return PersonalKnowledgeBaseAnswerResponse(answer=answer, results=results)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in personal KB tool-search webhook for agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
