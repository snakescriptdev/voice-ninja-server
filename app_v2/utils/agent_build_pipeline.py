import asyncio

from fastapi import HTTPException
from fastapi_sqlalchemy import db

from app_v2.core.logger import setup_logger
from app_v2.databases.models import (
    AgentBuildJobModel,
    AIModels,
    PersonalKnowledgeBaseAgentBridgeModel,
    VoiceModel,
)
from app_v2.routers.agents import create_agent_core
from app_v2.routers.personal_knowledge_base import _store_kb_entry
from app_v2.schemas.agent_schema import AgentCreate
from app_v2.schemas.enum_types import AgentBuildStatusEnum
from app_v2.utils.activity_logger import log_activity
from app_v2.utils.coin_utils import get_free_tier_defaults
from app_v2.utils.llm_utils import generate_system_prompt_from_instructions_async
from app_v2.utils.personal_kb_tool import ensure_personal_kb_tool_for_agent
from app_v2.utils.text_extraction import extract_text_from_file
from app_v2.utils.web_scraper import scrape_url

logger = setup_logger(__name__)


def run_agent_build_job(job_id: int, user_id: int) -> None:
    """Runs the "build me an agent" pipeline for a single job. Meant to be
    invoked inside a daemon thread (see agent_build.py) — `with db():` binds
    a fresh thread-local session for the duration of the run."""
    with db():
        job = db.session.get(AgentBuildJobModel, job_id)
        try:
            _set_status(job, AgentBuildStatusEnum.understanding_requirement)
            agent_name = f"Voice Agent {job.id}"

            _set_status(job, AgentBuildStatusEnum.generating_conversation)
            system_prompt = asyncio.run(generate_system_prompt_from_instructions_async(job.requirement))

            _set_status(job, AgentBuildStatusEnum.configuring_agent)
            ai_model_name, language_code = _resolve_agent_defaults()

            _set_status(job, AgentBuildStatusEnum.configuring_knowledge)
            kb_entry_ids = _ingest_knowledge_attachments(job)

            _set_status(job, AgentBuildStatusEnum.configuring_voice)
            voice_name = _resolve_voice_default()

            _set_status(job, AgentBuildStatusEnum.creating_voice_agent)
            agent_in = AgentCreate(
                agent_name=agent_name,
                # create_agent_core's DB insert writes this straight into a
                # NOT NULL column (only its ElevenLabs call has a built-in
                # "Hello! How can I help you?" fallback for None) — pass the
                # same fallback text explicitly here so the DB write doesn't
                # crash on this flow's omitted first_message.
                first_message="Hello! How can I help you?",
                system_prompt=system_prompt,
                voice=voice_name,
                ai_model=ai_model_name,
                language=language_code,
            )
            new_agent = asyncio.run(create_agent_core(agent_in, user_id))

            _set_status(job, AgentBuildStatusEnum.finalizing)
            job.agent_id = new_agent.id
            db.session.add(job)
            db.session.commit()

            _attach_knowledge_to_agent(kb_entry_ids, new_agent.id)

            log_activity(
                user_id=user_id,
                event_type="agent_build_completed",
                description=f"Home build created agent: {new_agent.agent_name}",
                metadata={"agent_id": new_agent.id, "build_job_id": job.id},
            )

            job.status = AgentBuildStatusEnum.completed
            db.session.add(job)
            db.session.commit()

        except Exception as e:
            # Full technical detail (stack trace, raw provider error text)
            # goes to the server log only — job.error_message is what the
            # Home page's error screen shows the user directly.
            logger.exception(f"Agent build job {job_id} failed")
            db.session.rollback()
            job = db.session.get(AgentBuildJobModel, job_id)
            job.status = AgentBuildStatusEnum.failed
            job.error_message = _friendly_error_message(e)
            db.session.add(job)
            db.session.commit()


def _ingest_knowledge_attachments(job: AgentBuildJobModel) -> list[int]:
    """Chunks/embeds the build's optional file/URL attachments into the
    user's personal KB (same store as app_v2/routers/personal_knowledge_base.py)
    so they can be bound to the agent once it exists. Each attachment is
    ingested independently and a bad one (unreadable file, unreachable URL)
    is logged and skipped rather than failing the whole build — the agent
    itself doesn't depend on knowledge ingestion succeeding.
    """
    kb_entry_ids: list[int] = []

    for file_info in job.knowledge_files or []:
        path = file_info.get("path")
        filename = file_info.get("filename", path)
        try:
            text = extract_text_from_file(path)
            kb_entry = _store_kb_entry(user_id=job.user_id, kb_type="file", title=filename, text=text, content_path=path)
            kb_entry_ids.append(kb_entry.id)
        except Exception as e:
            logger.warning(f"Agent build job {job.id}: skipping knowledge file '{filename}' ({e})")

    for url in job.knowledge_urls or []:
        try:
            title, text = scrape_url(url)
            kb_entry = _store_kb_entry(user_id=job.user_id, kb_type="url", title=title, text=text, content_path=url)
            kb_entry_ids.append(kb_entry.id)
        except Exception as e:
            logger.warning(f"Agent build job {job.id}: skipping knowledge URL '{url}' ({e})")

    return kb_entry_ids


def _attach_knowledge_to_agent(kb_entry_ids: list[int], agent_id: int) -> None:
    """Binds already-ingested personal KB entries to the newly created agent
    and provisions its search_personal_knowledge_base tool. Non-fatal on
    failure — the agent has already been created successfully by this point."""
    if not kb_entry_ids:
        return
    try:
        for kb_id in kb_entry_ids:
            db.session.add(PersonalKnowledgeBaseAgentBridgeModel(kb_id=kb_id, agent_id=agent_id))
        db.session.commit()
        ensure_personal_kb_tool_for_agent(agent_id)
    except Exception as e:
        logger.warning(f"Failed to attach knowledge attachments to agent {agent_id}: {e}")


def _friendly_error_message(e: Exception) -> str:
    """Turns whatever the pipeline raised into short, non-technical copy
    that's safe to show directly in the Home page's error screen. The full
    exception is always logged separately (see the except block above) — this
    is user-facing text only, not a diagnostic."""
    if isinstance(e, HTTPException):
        detail = e.detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("detail") or str(detail)
        return str(detail)[:500]

    text = str(e)
    lowered = text.lower()
    if "quota" in lowered or "resource_exhausted" in lowered or "rate limit" in lowered:
        return "We're experiencing high demand right now. Please try again in a few minutes."
    if "system prompt" in lowered or "generativelanguage" in lowered:
        return "We hit a problem writing your agent's conversation. Please try again."

    return "Something went wrong while building your agent. Please try again."


def _set_status(job: AgentBuildJobModel, status: AgentBuildStatusEnum) -> None:
    job.status = status
    db.session.add(job)
    db.session.commit()


DEFAULT_LANGUAGE_CODE = "en"


def _resolve_agent_defaults():
    """Returns (ai_model_name, language_code) for the build pipeline's default agent config."""
    free_model, _ = get_free_tier_defaults()
    if free_model:
        return free_model.model_name, DEFAULT_LANGUAGE_CODE
    first_model = db.session.query(AIModels).order_by(AIModels.id).first()
    if not first_model:
        raise RuntimeError("No AI models configured")
    return first_model.model_name, DEFAULT_LANGUAGE_CODE


def _resolve_voice_default() -> str:
    _, free_voice = get_free_tier_defaults()
    if free_voice:
        return free_voice.voice_name
    first_voice = (
        db.session.query(VoiceModel)
        .filter(VoiceModel.is_enabled.is_(True))
        .order_by(VoiceModel.id)
        .first()
    )
    if not first_voice:
        raise RuntimeError("No voices configured")
    return first_voice.voice_name
