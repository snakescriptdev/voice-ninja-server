import os
import shutil
import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
from app_v2.databases.models import AgentBuildJobModel, AgentModel, UnifiedAuthModel
from app_v2.routers.agents import agent_to_read
from app_v2.routers.personal_knowledge_base import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE_IN_MB,
    UPLOAD_DIR,
)
from app_v2.schemas.agent_build_schema import (
    MAX_KNOWLEDGE_FILES,
    MAX_KNOWLEDGE_URLS,
    AgentBuildJobOut,
    validate_requirement_text,
)
from app_v2.utils.agent_build_pipeline import run_agent_build_job
from app_v2.utils.feature_access import RequireFeature
from app_v2.utils.jwt_utils import require_active_user

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/agent-build",
    tags=["agent-build"],
)


def _clean_urls(urls: list[str]) -> list[str]:
    """Trims/dedupes/caps the optional knowledge-base URLs submitted with a
    build. Ingestion (scraping) happens later in the pipeline — this only
    validates shape so a bad request fails fast with a 400, not mid-build."""
    cleaned = []
    seen = set()
    for raw in urls:
        url = (raw or "").strip()
        if not url:
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail=f"'{url}' is not a valid http(s) URL")
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(url)

    if len(cleaned) > MAX_KNOWLEDGE_URLS:
        raise HTTPException(status_code=400, detail=f"You can attach at most {MAX_KNOWLEDGE_URLS} links")
    return cleaned


def _save_knowledge_files(user_id: int, job_id: int, files: list[UploadFile]) -> list[dict]:
    """Validates and persists the optional knowledge-base files submitted
    with a build to the same upload directory the personal-KB flow uses
    (see app_v2/routers/personal_knowledge_base.py) — the pipeline ingests
    them from there. Returns [{"path": ..., "filename": ...}, ...]."""
    real_files = [f for f in files if f.filename]
    if len(real_files) > MAX_KNOWLEDGE_FILES:
        raise HTTPException(status_code=400, detail=f"You can attach at most {MAX_KNOWLEDGE_FILES} files")

    saved = []
    for file in real_files:
        _, ext = os.path.splitext(file.filename)
        if ext.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt",
            )

        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
        if file_size == 0:
            raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")
        if file_size > MAX_FILE_SIZE_IN_MB * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"File {file.filename} exceeds {MAX_FILE_SIZE_IN_MB}MB limit",
            )

        file_path = os.path.join(
            UPLOAD_DIR, f"build_{user_id}_{job_id}_{datetime.now(UTC).timestamp()}_{file.filename}"
        )
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved.append({"path": file_path, "filename": file.filename})

    return saved


@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an AI-assisted agent build",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def start_agent_build(
    requirement: str = Form(..., description="Freeform description of the agent to build"),
    urls: list[str] = Form(default=[], description="Optional URLs the agent should learn from"),
    files: list[UploadFile] = File(default=[], description="Optional files the agent should learn from"),
    current_user: UnifiedAuthModel = Depends(RequireFeature("ai_voice_agents", allow_coin_fallback=True)),
):
    try:
        requirement = validate_requirement_text(requirement)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    clean_urls = _clean_urls(urls)

    job = AgentBuildJobModel(
        user_id=current_user.id,
        requirement=requirement,
    )
    db.session.add(job)
    db.session.commit()
    db.session.refresh(job)

    try:
        saved_files = _save_knowledge_files(current_user.id, job.id, files)
    except HTTPException:
        db.session.delete(job)
        db.session.commit()
        raise

    job.knowledge_urls = clean_urls
    job.knowledge_files = saved_files
    db.session.add(job)
    db.session.commit()

    threading.Thread(
        target=run_agent_build_job,
        args=(job.id, current_user.id),
        daemon=True,
        name=f"agent-build-{job.id}",
    ).start()

    return AgentBuildJobOut(id=job.id, status=job.status, error_message=None, agent=None)


@router.get(
    "/{job_id}",
    summary="Get the status of an agent build job",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_agent_build_job(
    job_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    job = (
        db.session.query(AgentBuildJobModel)
        .filter(AgentBuildJobModel.id == job_id, AgentBuildJobModel.user_id == current_user.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build job not found")

    agent_out = None
    if job.agent_id:
        agent = db.session.query(AgentModel).filter(AgentModel.id == job.agent_id).first()
        if agent:
            agent_out = agent_to_read(agent)

    return AgentBuildJobOut(id=job.id, status=job.status, error_message=job.error_message, agent=agent_out)
