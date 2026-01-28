"""
This module defines the CRUD Routes for agent.
"""

from sqlalchemy.orm import Session
from fastapi import HTTPException, status, APIRouter, Depends
from sqlalchemy import or_

from app_v2.dependecies import get_db
from app_v2.utils.jwt_utils import get_current_user,HTTPBearer
from app_v2.databases.models import (
    AgentModel,
    VoiceModel,
    AIModels,
    LanguageModel,
    AgentAIModelBridge,
    AgentLanguageBridge,
    UnifiedAuthModel
)
from app_v2.schemas.agent_schema import AgentCreate, AgentRead, AgentUpdate
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


router = APIRouter(
    prefix="/api/v2/agent",
    tags=["agent"]
)

security = HTTPBearer()


@router.post(
    "/",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create agent",
    openapi_extra={"security": [{"BearerAuth": []}]}
)
def create_agent_route(
    agent_in: AgentCreate,
    current_user: UnifiedAuthModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user_id = current_user.id

    voice_id = (
    db.query(VoiceModel.id)
    .filter(
        VoiceModel.voice_name == agent_in.voice,
        or_(
            VoiceModel.is_custom_voice.is_(False),
            VoiceModel.user_id == user_id
        )
    )
    .scalar()
)

    if not voice_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Voice not found"
        )

    # -------- Create Agent --------
    agent = AgentModel(
        agent_name=agent_in.agent_name,
        first_message=agent_in.first_message,
        system_prompt=agent_in.system_prompt,
        agent_voice=voice_id,
        user_id=user_id
    )

    db.add(agent)
    db.flush()  # gets agent.id without commit

    # -------- AI Models (M:M) --------
    ai_model_ids = (
        db.query(AIModels.id)
        .filter(AIModels.model_name.in_(agent_in.ai_models))
        .all()
    )

    if len(ai_model_ids) != len(agent_in.ai_models):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more AI models are invalid"
        )

    for (ai_model_id,) in ai_model_ids:
        db.add(
            AgentAIModelBridge(
                agent_id=agent.id,
                ai_model_id=ai_model_id
            )
        )

    # -------- Languages (M:M) --------
    lang_ids = (
        db.query(LanguageModel.id)
        .filter(LanguageModel.lang_code.in_(agent_in.languages))
        .all()
    )

    if len(lang_ids) != len(agent_in.languages):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more languages are invalid"
        )

    for (lang_id,) in lang_ids:
        db.add(
            AgentLanguageBridge(
                agent_id=agent.id,
                lang_id=lang_id
            )
        )

    db.commit()
    db.refresh(agent)

    return agent


@router.get(
    "/",
    response_model=list[AgentRead],
    status_code=status.HTTP_200_OK,
    summary="Get all agents of current user",
    openapi_extra={"security": [{"BearerAuth": []}]}
)
def get_all_agents(
    current_user: UnifiedAuthModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        agents = (
            db.query(AgentModel)
            .filter(AgentModel.user_id == current_user.id)
            .all()
        )

        logger.info(
            "Fetched %s agents for user_id=%s",
            len(agents),
            current_user.id
        )

        return agents

    except Exception as exc:
        logger.exception("Failed to fetch agents for user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch agents"
        )


@router.get(
    "/{agent_id}",
    response_model=AgentRead,
    status_code=status.HTTP_200_OK,
    summary="Get agent by ID",
    openapi_extra={"security": [{"BearerAuth": []}]}
)
def get_agent_by_id(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        agent = (
            db.query(AgentModel)
            .filter(
                AgentModel.id == agent_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not agent:
            logger.warning(
                "Agent not found: agent_id=%s user_id=%s",
                agent_id,
                current_user.id
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )

        return agent

    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to fetch agent_id=%s", agent_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch agent"
        )



@router.put(
    "/{agent_id}",
    response_model=AgentRead,
    status_code=status.HTTP_200_OK,
    summary="Update agent (partial update)",
    openapi_extra={"security": [{"BearerAuth": []}]}
)
def update_agent(
    agent_id: int,
    agent_in: AgentUpdate,
    current_user: UnifiedAuthModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        # ---- Fetch Agent ----
        agent = (
            db.query(AgentModel)
            .filter(
                AgentModel.id == agent_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not agent:
            logger.warning(
                "Update failed. Agent not found",
                extra={"agent_id": agent_id, "user_id": current_user.id}
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )

        # ---- Update Voice ----
        if agent_in.voice is not None:
            voice_id = (
                db.query(VoiceModel.id)
                .filter(
                    VoiceModel.voice_name == agent_in.voice,
                    or_(
                        VoiceModel.is_custom_voice.is_(False),
                        VoiceModel.user_id == current_user.id
                    )
                )
                .scalar()
            )

            if not voice_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Voice not found"
                )

            agent.agent_voice = voice_id

        # ---- Update Base Fields ----
        if agent_in.agent_name is not None:
            agent.agent_name = agent_in.agent_name

        if agent_in.first_message is not None:
            agent.first_message = agent_in.first_message

        if agent_in.system_prompt is not None:
            agent.system_prompt = agent_in.system_prompt

        # ---- Update AI Models (M:M) ----
        if agent_in.ai_models is not None:
            if not agent_in.ai_models:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="ai_models cannot be empty"
                )

            db.query(AgentAIModelBridge).filter(
                AgentAIModelBridge.agent_id == agent_id
            ).delete()

            ai_model_ids = (
                db.query(AIModels.id)
                .filter(AIModels.model_name.in_(agent_in.ai_models))
                .all()
            )

            if len(ai_model_ids) != len(agent_in.ai_models):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One or more AI models are invalid"
                )

            for (ai_model_id,) in ai_model_ids:
                db.add(
                    AgentAIModelBridge(
                        agent_id=agent_id,
                        ai_model_id=ai_model_id
                    )
                )

        # ---- Update Languages (M:M) ----
        if agent_in.languages is not None:
            if not agent_in.languages:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="languages cannot be empty"
                )

            db.query(AgentLanguageBridge).filter(
                AgentLanguageBridge.agent_id == agent_id
            ).delete()

            lang_ids = (
                db.query(LanguageModel.id)
                .filter(LanguageModel.lang_code.in_(agent_in.languages))
                .all()
            )

            if len(lang_ids) != len(agent_in.languages):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One or more languages are invalid"
                )

            for (lang_id,) in lang_ids:
                db.add(
                    AgentLanguageBridge(
                        agent_id=agent_id,
                        lang_id=lang_id
                    )
                )

        db.commit()
        db.refresh(agent)

        logger.info(
            "Agent updated successfully",
            extra={"agent_id": agent_id, "user_id": current_user.id}
        )

        return agent

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        logger.exception(
            "Unexpected error while updating agent",
            extra={"agent_id": agent_id, "user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update agent"
        )



@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete agent",
    openapi_extra={"security": [{"BearerAuth": []}]}
)
def delete_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        agent = (
            db.query(AgentModel)
            .filter(
                AgentModel.id == agent_id,
                AgentModel.user_id == current_user.id
            )
            .first()
        )

        if not agent:
            logger.warning(
                "Delete failed. Agent not found: agent_id=%s user_id=%s",
                agent_id,
                current_user.id
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )

        # ---- Delete M:M First ----
        db.query(AgentAIModelBridge).filter(
            AgentAIModelBridge.agent_id == agent_id
        ).delete()

        db.query(AgentLanguageBridge).filter(
            AgentLanguageBridge.agent_id == agent_id
        ).delete()

        db.delete(agent)
        db.commit()

        logger.info(
            "Agent deleted successfully: agent_id=%s user_id=%s",
            agent_id,
            current_user.id
        )

        return None

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to delete agent_id=%s", agent_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete agent"
        )
