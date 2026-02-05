from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from fastapi_sqlalchemy import db
from app_v2.schemas.agent_config import AgentConfigGenerator, AgentConfigOut
from app_v2.schemas.pagination import PaginatedResponse
import math
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
from app_v2.utils.elevenlabs import ElevenLabsAgent

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/agent",
    tags=["agent"],
)

security = HTTPBearer()


from sqlalchemy.orm import selectinload

# ... (other imports)

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
        ai_model=ai_model,
        language=language,
        updated_at=agent.modified_at,
        phone = agent.phone,
        elevenlabs_agent_id=agent.elevenlabs_agent_id
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
    voice = (
        db.session.query(VoiceModel)
        .filter(
            VoiceModel.voice_name == agent_in.voice,
            or_(
                VoiceModel.is_custom_voice.is_(False),
                VoiceModel.user_id == user_id,
            ),
        )
        .first()
    )

    if not voice:
        raise HTTPException(status_code=400, detail="Voice not found")

    # ---- AI Model (single) ----
    ai_model = (
        db.session.query(AIModels)
        .filter(AIModels.model_name == agent_in.ai_models)
        .first()
    )

    if not ai_model:
        raise HTTPException(status_code=400, detail="Invalid AI model")

    # ---- Language (single) ----
    language = (
        db.session.query(LanguageModel)
        .filter(LanguageModel.lang_code == agent_in.languages)
        .first()
    )

    if not language:
        raise HTTPException(status_code=400, detail="Invalid language code")

    # ---- Synchronize with ElevenLabs ----
    elevenlabs_agent_id = None
    try:
        logger.info(f"Creating agent '{agent_in.agent_name}' in ElevenLabs for user {user_id}")
        el_client = ElevenLabsAgent()
        
        # Check if the voice has an elevenlabs_voice_id
        el_voice_id = voice.elevenlabs_voice_id
        if not el_voice_id:
            logger.error(f"Voice '{voice.voice_name}' (ID: {voice.id}) is missing ElevenLabs voice ID")
            raise HTTPException(
                status_code=424,
                detail=f"Selected voice '{voice.voice_name}' is not properly synchronized with ElevenLabs"
            )

        el_response = el_client.create_agent(
            name=agent_in.agent_name,
            voice_id=el_voice_id,
            prompt=agent_in.system_prompt,
            first_message=agent_in.first_message or "Hello! How can I help you?",
            language=language.lang_code,
            llm_model=ai_model.model_name
        )

        if el_response.status:
            elevenlabs_agent_id = el_response.data.get("agent_id")
            logger.info(f"✅ Agent created in ElevenLabs with ID: {elevenlabs_agent_id}")
        else:
            error_msg = el_response.error_message or "Failed to create agent in ElevenLabs"
            logger.error(f"❌ ElevenLabs agent creation failed: {error_msg}")
            raise HTTPException(
                status_code=424,
                detail=f"Failed to create agent in ElevenLabs: {error_msg}"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during ElevenLabs agent creation: {e}")
        raise HTTPException(
            status_code=424,
            detail=f"Failed to create agent in ElevenLabs due to an unexpected error: {str(e)}"
        )

    # ---- Database Creation ----
    agent = AgentModel(
        agent_name=agent_in.agent_name,
        first_message=agent_in.first_message,
        system_prompt=agent_in.system_prompt,
        agent_voice=voice.id,
        user_id=user_id,
        phone=agent_in.phone,
        elevenlabs_agent_id=elevenlabs_agent_id
    )

    db.session.add(agent)
    db.session.flush()

    db.session.add(
        AgentAIModelBridge(
            agent_id=agent.id,
            ai_model_id=ai_model.id,
        )
    )

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
    response_model=PaginatedResponse[AgentRead],
    summary="Get all agents",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_all_agents(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    if page < 1:
        page = 1
    
    skip = (page - 1) * size
    
    query = (
        db.session.query(AgentModel)
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice)
        )
        .filter(AgentModel.user_id == current_user.id)
    )
    
    total = query.count()
    pages = math.ceil(total / size)
    
    agents = (
        query
        .offset(skip)
        .limit(size)
        .all()
    )

    items = [agent_to_read(agent) for agent in agents]
    
    return PaginatedResponse(
        total=total,
        page=page,
        size=size,
        pages=pages,
        items=items
    )


# -------------------- GET BY ID --------------------

@router.get(
    "by-id/{agent_id}",
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
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice)
        )
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

    # ---- ElevenLabs Synchronization Preparation ----
    el_update_params = {}
    
    # ---- Base Fields ----
    if agent_in.agent_name is not None:
        agent.agent_name = agent_in.agent_name
        el_update_params["name"] = agent_in.agent_name
    if agent_in.first_message is not None:
        agent.first_message = agent_in.first_message
        el_update_params["first_message"] = agent_in.first_message
    if agent_in.system_prompt is not None:
        agent.system_prompt = agent_in.system_prompt
        el_update_params["prompt"] = agent_in.system_prompt

    # ---- Voice ----
    if agent_in.voice is not None:
        voice = (
            db.session.query(VoiceModel)
            .filter(VoiceModel.voice_name == agent_in.voice)
            .first()
        )
        if not voice:
            raise HTTPException(status_code=400, detail="Voice not found")
        agent.agent_voice = voice.id
        
        if voice.elevenlabs_voice_id:
            el_update_params["voice_id"] = voice.elevenlabs_voice_id
        else:
            logger.warning(f"Voice '{voice.voice_name}' (ID: {voice.id}) is missing ElevenLabs voice ID")

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
        el_update_params["llm_model"] = ai_model.model_name

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
        el_update_params["language"] = language.lang_code

    # ---- Sync with ElevenLabs ----
    if el_update_params and agent.elevenlabs_agent_id:
        try:
            logger.info(f"Updating agent '{agent.elevenlabs_agent_id}' in ElevenLabs")
            el_client = ElevenLabsAgent()
            el_response = el_client.update_agent(
                agent_id=agent.elevenlabs_agent_id,
                **el_update_params
            )
            
            if not el_response.status:
                logger.error(f"❌ ElevenLabs agent update failed: {el_response.error_message}")
                # We decide whether to fail the database update or just log the error.
                # Since the local DB is the source of truth for our UI, we might want to keep it in sync,
                # but ElevenLabs being out of sync is a problem.
                # Given the voice router pattern, let's raise an error.
                raise HTTPException(
                    status_code=424,
                    detail=f"Failed to update agent in ElevenLabs: {el_response.error_message}"
                )
            logger.info(f"✅ ElevenLabs agent '{agent.elevenlabs_agent_id}' updated successfully")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during ElevenLabs agent update: {e}")
            raise HTTPException(
                status_code=424,
                detail=f"Failed to update agent in ElevenLabs due to an unexpected error: {str(e)}"
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

    # ---- Delete from ElevenLabs ----
    if agent.elevenlabs_agent_id:
        try:
            logger.info(f"Deleting agent from ElevenLabs: {agent.elevenlabs_agent_id}")
            el_client = ElevenLabsAgent()
            el_response = el_client.delete_agent(agent.elevenlabs_agent_id)
            
            if el_response.status:
                logger.info(f"✅ Agent deleted from ElevenLabs: {agent.elevenlabs_agent_id}")
            else:
                logger.warning(f"Failed to delete agent from ElevenLabs: {el_response.error_message}")
                # We continue with database deletion even if ElevenLabs deletion fails
        except Exception as e:
            logger.error(f"Error deleting agent from ElevenLabs: {e}")
            # We continue with database deletion even if ElevenLabs deletion fails

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
                detail=f"failed to generate system prompt at the moment: {str(e)}"
            )