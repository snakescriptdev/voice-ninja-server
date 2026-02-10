from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from fastapi_sqlalchemy import db
from app_v2.schemas.agent_config import AgentConfigGenerator, AgentConfigOut
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.enum_types import PhoneNumberAssignStatus
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
    PhoneNumberService
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

    phone_number = (
        agent.phone_number[0].phone_number
        if agent.phone_number else None
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
        elevenlabs_agent_id=agent.elevenlabs_agent_id,
        phone = phone_number
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

    #check for agent existence 
    agent_exists = (
        db.session.query(AgentModel).filter(
            AgentModel.agent_name ==agent_in.agent_name,
            AgentModel.user_id == user_id
        ).first()
    )

    if agent_exists:
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail= "Agent with this name already exists"
        )

    # -------------------------------------------------
    # Voice validation: only allow voices that are synced with ElevenLabs
    # -------------------------------------------------
    voice = (
        db.session.query(VoiceModel)
        .filter(
            VoiceModel.voice_name == agent_in.voice,
            VoiceModel.elevenlabs_voice_id.isnot(None),
            or_(
                VoiceModel.is_custom_voice.is_(False),
                VoiceModel.user_id == user_id,
            ),
        )
        .first()
    )

    if not voice:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Voice '{agent_in.voice}' not found or not synced with ElevenLabs",
                "hint": "Run: python populate_elevenlabs_data.py to sync voices, then use a voice from the list.",
            },
        )

    # -------------------------------------------------
    # AI Model validation (single)
    # -------------------------------------------------
    ai_model = (
        db.session.query(AIModels)
        .filter(AIModels.model_name == agent_in.ai_model)
        .first()
    )

    if not ai_model:
        raise HTTPException(status_code=400, detail="Invalid AI model")

    # -------------------------------------------------
    # Language validation (single)
    # -------------------------------------------------
    language = (
        db.session.query(LanguageModel)
        .filter(LanguageModel.lang_code == agent_in.language)
        .first()
    )

    if not language:
        raise HTTPException(status_code=400, detail="Invalid language code")

    # -------------------------------------------------
    # Phone number lookup & validation 
    # -------------------------------------------------
    phone_record = None
    if agent_in.phone:
        phone_record = (
            db.session.query(PhoneNumberService)
            .filter(
                PhoneNumberService.phone_number == agent_in.phone,
                PhoneNumberService.user_id == user_id,
            )
            .first()
        )

        if not phone_record:
            raise HTTPException(
                status_code=404,
                detail=f"Phone number {agent_in.phone} not found or not owned by you"
            )

        if phone_record.assigned_to is not None:
            raise HTTPException(
                status_code=400,
                detail=f"Phone number {agent_in.phone} is already assigned to another agent"
            )

    # -------------------------------------------------
    # Create agent in ElevenLabs (only after validation)
    # -------------------------------------------------
    elevenlabs_agent_id = None
    el_client = ElevenLabsAgent()

    try:
        logger.info(
            f"Creating agent '{agent_in.agent_name}' in ElevenLabs for user {user_id}"
        )

        el_response = el_client.create_agent(
            name=agent_in.agent_name,
            voice_id=voice.elevenlabs_voice_id,
            prompt=agent_in.system_prompt,
            first_message=agent_in.first_message or "Hello! How can I help you?",
            language=language.lang_code,
            llm_model=ai_model.model_name,
        )

        if not el_response.status:
            raise HTTPException(
                status_code=424,
                detail=el_response.error_message or "Failed to create agent in ElevenLabs",
            )

        elevenlabs_agent_id = el_response.data.get("agent_id")
        logger.info(f"✅ ElevenLabs agent created: {elevenlabs_agent_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected ElevenLabs error")
        raise HTTPException(
            status_code=424,
            detail=f"Unexpected error while creating agent in ElevenLabs {str(e)}",
        )

    # -------------------------------------------------
    # Database creation (atomic)
    # -------------------------------------------------
    try:
        agent = AgentModel(
            agent_name=agent_in.agent_name,
            first_message=agent_in.first_message,
            system_prompt=agent_in.system_prompt,
            agent_voice=voice.id,
            user_id=user_id,
            elevenlabs_agent_id=elevenlabs_agent_id,
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

        if phone_record:
            phone_record.assigned_to = agent.id
            phone_record.status = PhoneNumberAssignStatus.assigned
            logger.info(
                f"Assigned phone {phone_record.phone_number} to agent {agent.agent_name}"
            )

        db.session.commit()
        db.session.refresh(agent)
    except Exception as db_error:
        db.session.rollback()
        if elevenlabs_agent_id:
            try:
                el_client.delete_agent(elevenlabs_agent_id)
                logger.info(f"Cleaned up ElevenLabs agent {elevenlabs_agent_id} after DB failure")
            except Exception as cleanup_err:
                logger.warning(f"Failed to delete orphan ElevenLabs agent {elevenlabs_agent_id}: {cleanup_err}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save agent: {str(db_error)}",
        )

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
            selectinload(AgentModel.voice),
            selectinload(AgentModel.phone_number)
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

#made for admin to get any agent
@router.get(
    "by-id/{agent_id}",
    response_model=AgentRead,
    summary="Get agent by ID",
)
async def get_agent_by_id(
    agent_id: int,
):
    agent = (
        db.session.query(AgentModel)
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice),
            selectinload(AgentModel.phone_number)
        )
        .filter(
            AgentModel.id == agent_id,
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
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice),
            selectinload(AgentModel.phone_number)
        )
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
    
    # ---- Phone Number Update ----
    if agent_in.phone is not None:
        # First, unassign any currently assigned phone
        old_phone = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.assigned_to == agent_id
        ).first()
        
        if old_phone:
            old_phone.assigned_to = None
            old_phone.status = PhoneNumberAssignStatus.unassigned
            logger.info(f"Unassigned phone {old_phone.phone_number} from agent {agent.agent_name}")
        
        # Now assign new phone if provided (empty string means unassign only)
        if agent_in.phone and agent_in.phone.strip():
            # Lookup phone by phone number string
            new_phone = db.session.query(PhoneNumberService).filter(
                PhoneNumberService.phone_number == agent_in.phone,
                PhoneNumberService.user_id == current_user.id
            ).first()
            
            if not new_phone:
                raise HTTPException(status_code=404, detail=f"Phone number {agent_in.phone} not found or not owned by you")
            
            if new_phone.assigned_to is not None and new_phone.assigned_to != agent_id:
                raise HTTPException(status_code=400, detail=f"Phone number {agent_in.phone} is already assigned to another agent")
            
            new_phone.assigned_to = agent_id
            new_phone.status = PhoneNumberAssignStatus.assigned
            logger.info(f"Assigned phone {new_phone.phone_number} to agent {agent.agent_name}")
        # else: empty string means unassign only (already done above)
    
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
            .filter(
                VoiceModel.voice_name == agent_in.voice,
                VoiceModel.elevenlabs_voice_id.isnot(None),
                or_(
                    VoiceModel.user_id == current_user.id,
                    VoiceModel.user_id.is_(None),
                ),
            )
            .first()
        )
        if not voice:
            raise HTTPException(
                status_code=400,
                detail=f"Voice '{agent_in.voice}' not found or not synced with ElevenLabs. Run: python populate_elevenlabs_data.py",
            )
        agent.agent_voice = voice.id
        el_update_params["voice_id"] = voice.elevenlabs_voice_id

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
                agent_id=agent.id,
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
                db.session.rollback()
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

    # ---- Unassign phone number first ----
    assigned_phone = db.session.query(PhoneNumberService).filter(
        PhoneNumberService.assigned_to == agent_id
    ).first()
    
    if assigned_phone:
        assigned_phone.assigned_to = None
        assigned_phone.status = PhoneNumberAssignStatus.unassigned
        logger.info(f"Unassigned phone {assigned_phone.phone_number} from agent {agent.agent_name}")
        db.session.commit()  # Commit phone unassignment before attempting ElevenLabs deletion

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
                # if not deleted from elevenlabs then rollback the database
                db.session.rollback()
                raise HTTPException(
                    status_code=424,
                    detail=f"Failed to delete agent from ElevenLabs: {el_response.error_message}"
                )
        except Exception as e:
            logger.error(f"Error deleting agent from ElevenLabs: {e}")
            db.session.rollback()
            raise HTTPException(
                status_code=424,
                detail=f"Failed to delete agent from ElevenLabs: {str(e)}"
            )

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