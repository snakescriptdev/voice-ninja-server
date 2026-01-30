from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from fastapi_sqlalchemy import db
from app_v2.schemas.agent_config import AgentConfigGenerator, AgentConfigOut
from app_v2.utils.llm_utils import generate_system_prompt_async

from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import (
    AgentModel,
    VoiceModel,
    AIModels,
    LanguageModel,
    AgentAIModelBridge,
    AgentLanguageBridge,
    UnifiedAuthModel,
)
from app_v2.schemas.agent_schema import AgentCreate, AgentRead, AgentUpdate
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/agent",
    tags=["agent"],
)

security = HTTPBearer()


# -------------------- RESPONSE MAPPER --------------------

def agent_to_read(agent: AgentModel) -> AgentRead:
    ai_model = (
        agent.agent_ai_models[0].ai_model.model_name
        if agent.agent_ai_models else None
    )
    language = (
        agent.agent_languages[0].language.lang_code
        if agent.agent_languages else None
    )

    return AgentRead(
        id=agent.id,
        agent_name=agent.agent_name,
        first_message=agent.first_message,
        system_prompt=agent.system_prompt,
        voice=agent.voice.voice_name,
        ai_models=ai_model,
        languages=language,
    )


# -------------------- CREATE --------------------

@router.post(
    "/",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def create_agent(
    agent_in: AgentCreate,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    user_id = current_user.id

    # ---- Voice ----
    voice_id = (
        db.session.query(VoiceModel.id)
        .filter(
            VoiceModel.voice_name == agent_in.voice,
            or_(
                VoiceModel.is_custom_voice.is_(False),
                VoiceModel.user_id == user_id,
            ),
        )
        .scalar()
    )

    if not voice_id:
        raise HTTPException(status_code=400, detail="Voice not found")

    agent = AgentModel(
        agent_name=agent_in.agent_name,
        first_message=agent_in.first_message,
        system_prompt=agent_in.system_prompt,
        agent_voice=voice_id,
        user_id=user_id,
    )

    db.session.add(agent)
    db.session.flush()

    # ---- AI Model (single) ----
    ai_model = (
        db.session.query(AIModels)
        .filter(AIModels.model_name == agent_in.ai_models)
        .first()
    )

    if not ai_model:
        raise HTTPException(status_code=400, detail="Invalid AI model")

    db.session.add(
        AgentAIModelBridge(
            agent_id=agent.id,
            ai_model_id=ai_model.id,
        )
    )

    # ---- Language (single) ----
    language = (
        db.session.query(LanguageModel)
        .filter(LanguageModel.lang_code == agent_in.languages)
        .first()
    )

    if not language:
        raise HTTPException(status_code=400, detail="Invalid language code")

    db.session.add(
        AgentLanguageBridge(
            agent_id=agent.id,
            lang_id=language.id,
        )
    )

    db.session.commit()
    db.session.refresh(agent)

    return agent_to_read(agent)


# -------------------- GET ALL --------------------

@router.get(
    "/",
    response_model=list[AgentRead],
    summary="Get all agents",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_all_agents(
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    agents = (
        db.session.query(AgentModel)
        .filter(AgentModel.user_id == current_user.id)
        .all()
    )

    return [agent_to_read(agent) for agent in agents]


# -------------------- GET BY ID --------------------

@router.get(
    "/{agent_id}",
    response_model=AgentRead,
    summary="Get agent by ID",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_agent_by_id(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    agent = (
        db.session.query(AgentModel)
        .filter(
            AgentModel.id == agent_id,
            AgentModel.user_id == current_user.id,
        )
        .first()
    )

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return agent_to_read(agent)


# -------------------- UPDATE --------------------

@router.put(
    "/{agent_id}",
    response_model=AgentRead,
    summary="Update agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_agent(
    agent_id: int,
    agent_in: AgentUpdate,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    agent = (
        db.session.query(AgentModel)
        .filter(
            AgentModel.id == agent_id,
            AgentModel.user_id == current_user.id,
        )
        .first()
    )

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ---- Base Fields ----
    if agent_in.agent_name is not None:
        agent.agent_name = agent_in.agent_name
    if agent_in.first_message is not None:
        agent.first_message = agent_in.first_message
    if agent_in.system_prompt is not None:
        agent.system_prompt = agent_in.system_prompt

    # ---- Voice ----
    if agent_in.voice is not None:
        voice_id = (
            db.session.query(VoiceModel.id)
            .filter(VoiceModel.voice_name == agent_in.voice)
            .scalar()
        )
        if not voice_id:
            raise HTTPException(status_code=400, detail="Voice not found")
        agent.agent_voice = voice_id

    # ---- AI Model ----
    if agent_in.ai_models is not None:
        db.session.query(AgentAIModelBridge).filter(
            AgentAIModelBridge.agent_id == agent_id
        ).delete()

        ai_model = (
            db.session.query(AIModels)
            .filter(AIModels.model_name == agent_in.ai_models)
            .first()
        )

        if not ai_model:
            raise HTTPException(status_code=400, detail="Invalid AI model")

        db.session.add(
            AgentAIModelBridge(
                agent_id=agent_id,
                ai_model_id=ai_model.id,
            )
        )

    # ---- Language ----
    if agent_in.languages is not None:
        db.session.query(AgentLanguageBridge).filter(
            AgentLanguageBridge.agent_id == agent_id
        ).delete()

        language = (
            db.session.query(LanguageModel)
            .filter(LanguageModel.lang_code == agent_in.languages)
            .first()
        )

        if not language:
            raise HTTPException(status_code=400, detail="Invalid language code")

        db.session.add(
            AgentLanguageBridge(
                agent_id=agent_id,
                lang_id=language.id,
            )
        )

    db.session.commit()
    db.session.refresh(agent)

    return agent_to_read(agent)


# -------------------- DELETE --------------------

@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def delete_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    agent = (
        db.session.query(AgentModel)
        .filter(
            AgentModel.id == agent_id,
            AgentModel.user_id == current_user.id,
        )
        .first()
    )

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    db.session.delete(agent)
    db.session.commit()


@router.post("/config",response_model=AgentConfigOut,status_code=status.HTTP_200_OK)
async def generate_system_prompt_for_agent(agent_config:AgentConfigGenerator):
        try:
            system_prompt =  await generate_system_prompt_async(agent_config)
            
            if not system_prompt:
                logger.error("failed to generate system prompt")
                raise HTTPException(
                    status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="could not generate system prompt at the moment"
                )
            
            response_config = AgentConfigOut(
                agent_name=agent_config.agent_name,
                ai_model=agent_config.ai_model,
                voice=agent_config.voice,
                language=agent_config.language,
                system_prompt=system_prompt,
            )
            logger.info("system prompt generated successfully")

            return response_config
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"error while genreating system prompt {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="failed to generate system prompt at the moment"
            )