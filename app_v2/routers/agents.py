from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fastapi_sqlalchemy import db
from app_v2.schemas.agent_config import AgentConfigGenerator, AgentConfigOut
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.enum_types import PhoneNumberAssignStatus
import math
from app_v2.utils.llm_utils import generate_system_prompt_async

from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.databases.models import (
    AdminTokenModel,
    VoiceTraitsModel,
    TokensToConsume,
    AgentModel,
    VoiceModel,
    AIModels,
    LanguageModel,
    AgentAIModelBridge,
    AgentLanguageBridge,
    UnifiedAuthModel,
    PhoneNumberService,
    KnowledgeBaseModel,
    AgentKnowledgeBaseBridge,
    AgentFunctionBridgeModel,
    FunctionModel,
    VariablesModel
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
        phone=phone_number,
        knowledgebase = [
            {
                "id": bridge.knowledge_base.id,
                "title": bridge.knowledge_base.title,
                "type": bridge.knowledge_base.kb_type
            }
            for bridge in agent.agent_knowledge_bases
        ],
        variables={var.variable_name: var.variable_value for var in agent.variables},
        tools=[
            {
                "id": bridge.function.id,
                "name": bridge.function.name
            } 
            for bridge in agent.agent_functions
        ],
        built_in_tools=agent.built_in_tools
    )


# -------------------- HELPERS --------------------

def transform_built_in_tools(built_in_tools_params, session: Session, user_id: int) -> dict:
    """Transform schema params to ElevenLabs payload structure."""
    if not built_in_tools_params:
        return None
        
    el_tools = {}
    
    # End Call
    if built_in_tools_params.end_call:
        config = built_in_tools_params.end_call
        if isinstance(config, bool):
            el_tools["end_call"] = {
                "name": "end_call",
                "params": {"system_tool_type": "end_call"}
            }
        else: # ToolConfig object
            el_tools["end_call"] = {
                "name": config.name or "end_call",
                "params": {"system_tool_type": "end_call"}
            }

    # Transfer to Agent
    if built_in_tools_params.transfer_to_agent:
        config = built_in_tools_params.transfer_to_agent
        if config.enabled:
            el_transfers = []
            for t in config.transfers:
                transfer_data = t.model_dump()
                requested_id = str(transfer_data.get("agent_id"))
                
                # Enforce numeric ID for internal lookups
                if not requested_id.isdigit():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Agent ID '{requested_id}' must be an internal numeric ID for transfer to agent tool"
                    )

                # Dynamic lookup: find agent by internal ID
                target_agent = session.query(AgentModel).filter(
                    AgentModel.id == int(requested_id),
                    AgentModel.user_id == user_id
                ).first()
                
                if target_agent and target_agent.elevenlabs_agent_id:
                    transfer_data["agent_id"] = target_agent.elevenlabs_agent_id
                    logger.info(f"Resolved agent transfer ID: {requested_id} -> {target_agent.elevenlabs_agent_id}")
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Agent with internal ID {requested_id} not found or missing ElevenLabs ID"
                    )
                
                el_transfers.append(transfer_data)

            el_tools["transfer_to_agent"] = {
                "name": config.name or "agent-transfer",
                "params": {
                    "system_tool_type": "transfer_to_agent",
                    "transfers": el_transfers
                }
            }
            
    # Transfer to Number
    if built_in_tools_params.transfer_to_number:
        config = built_in_tools_params.transfer_to_number
        if config.enabled:
            el_transfers = []
            for t in config.transfers:
                transfer_data = t.model_dump()
                phone_number = transfer_data.get("transfer_destination", {}).get("phone_number")
                
                # Ownership verification: ensure number belongs to user and PhoneNumberService
                db_phone = session.query(PhoneNumberService).filter(
                    PhoneNumberService.phone_number == phone_number,
                    PhoneNumberService.user_id == user_id
                ).first()
                
                if not db_phone:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Phone number '{phone_number}' does not belong to your account for transfer to number tool"
                    )
                
                el_transfers.append(transfer_data)

            el_tools["transfer_to_number"] = {
                "name": config.name or "transfer_to_number",
                "params": {
                    "system_tool_type": "transfer_to_number",
                    "transfers": el_transfers
                }
            }

    # DTMF / Keypad
    if built_in_tools_params.play_keypad_touch_tone:
        config = built_in_tools_params.play_keypad_touch_tone
        if isinstance(config, bool):
             el_tools["play_keypad_touch_tone"] = {
                "name": "play_keypad_touch_tone",
                "params": {"system_tool_type": "play_keypad_touch_tone"}
            }
        else:
             el_tools["play_keypad_touch_tone"] = {
                "name": config.name or "play_keypad_touch_tone",
                "params": {"system_tool_type": "play_keypad_touch_tone"}
            }

    return el_tools if el_tools else None


# -------------------- CREATE --------------------

@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Create agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def create_agent(
    agent_in: AgentCreate,
    current_user: UnifiedAuthModel = Depends(get_current_user),
):
    user_id = current_user.id
    
    #removed the name uniqueness constraint may switch in future

    # #check for agent existence 
    # agent_exists = (
    #     db.session.query(AgentModel).filter(
    #         AgentModel.agent_name ==agent_in.agent_name,
    #         AgentModel.user_id == user_id
    #     ).first()
    # )

    # if agent_exists:
    #     raise HTTPException(
    #         status_code= status.HTTP_400_BAD_REQUEST,
    #         detail= "Agent with this name already exists"
    #     )

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
    # KB & Tools validation and lookup
    # -------------------------------------------------
    el_kb_list = []
    kb_ids_ordered = []
    
    if agent_in.knowledgebase:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
        kb_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order
        
        # 2. Fetch from DB
        kb_records = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id.in_(kb_ids_ordered),
            KnowledgeBaseModel.user_id == user_id,
            KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
        ).all()
        
        # 3. Create a map for O(1) lookup
        kb_map = {kb.id: kb for kb in kb_records}
        
        # 4. Validate all IDs exist (checking against the unique set of requested IDs)
        found_ids = set(kb_map.keys())
        missing_ids = set(kb_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Knowledge Base IDs not found or not synced: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        for kb_id in kb_ids_ordered:
            kb = kb_map[kb_id]
            el_kb_list.append({
                "id": kb.elevenlabs_document_id,
                "type": "file", # ElevenLabs conversational AI usually treats them as files
                "name": kb.title or f"KB_{kb.id}"
            })

    el_tool_ids = []
    tool_ids_ordered = []

    if agent_in.tools:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
        tool_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order

        # 2. Fetch from DB
        tool_records = db.session.query(FunctionModel).filter(
            FunctionModel.id.in_(tool_ids_ordered),
            FunctionModel.elevenlabs_tool_id.isnot(None),
            or_(
                FunctionModel.user_id == user_id,
                FunctionModel.user_id.is_(None)
            )
        ).all()
        
        # 3. Create a map for O(1) lookup
        tool_map = {tool.id: tool for tool in tool_records}

        # 4. Validate all IDs exist
        found_ids = set(tool_map.keys())
        missing_ids = set(tool_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Tool IDs not found or not synced or not accessible to you: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        for tool_id in tool_ids_ordered:
            tool = tool_map[tool_id]
            el_tool_ids.append(tool.elevenlabs_tool_id)

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
            tool_ids=el_tool_ids,
            knowledge_base=el_kb_list,
            dynamic_variables=agent_in.variables,
            built_in_tools=transform_built_in_tools(agent_in.built_in_tools, db.session, user_id)
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
        new_agent = AgentModel(
            agent_name=agent_in.agent_name,
            first_message=agent_in.first_message,
            system_prompt=agent_in.system_prompt,
            user_id=user_id,
            agent_voice=voice.id,
            elevenlabs_agent_id=elevenlabs_agent_id,
            built_in_tools=agent_in.built_in_tools.model_dump() if agent_in.built_in_tools else {}
        )

        db.session.add(new_agent)
        db.session.flush()

        # Bridge: AI Model
        db.session.add(
            AgentAIModelBridge(
                agent_id=new_agent.id,
                ai_model_id=ai_model.id,
            )
        )

        # Bridge: Language
        db.session.add(
            AgentLanguageBridge(
                agent_id=new_agent.id,
                lang_id=language.id,
            )
        )

        # Bridge: Knowledge Base
        for kb_id in kb_ids_ordered:
            db.session.add(AgentKnowledgeBaseBridge(agent_id=new_agent.id, kb_id=kb_id))

        # Bridge: Tools
        for tool_id in tool_ids_ordered:
            db.session.add(AgentFunctionBridgeModel(agent_id=new_agent.id, function_id=tool_id))

        # Variables
        for key, value in (agent_in.variables or {}).items():
            db.session.add(VariablesModel(agent_id=new_agent.id, variable_name=key, variable_value=value))

        if phone_record:
            phone_record.assigned_to = new_agent.id
            phone_record.status = PhoneNumberAssignStatus.assigned
            logger.info(
                f"Assigned phone {phone_record.phone_number} to agent {new_agent.agent_name}"
            )

        db.session.commit()
        db.session.refresh(new_agent)
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

    return agent_to_read(new_agent)

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
            selectinload(AgentModel.phone_number),
            selectinload(AgentModel.variables),
            selectinload(AgentModel.agent_knowledge_bases),
            selectinload(AgentModel.agent_functions)
        )
        .filter(AgentModel.user_id == current_user.id)
        .order_by(AgentModel.modified_at.desc())
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
            selectinload(AgentModel.phone_number),
            selectinload(AgentModel.variables),
            selectinload(AgentModel.agent_knowledge_bases),
            selectinload(AgentModel.agent_functions)
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
    if agent_in.ai_model is not None:
        db.session.query(AgentAIModelBridge).filter(
            AgentAIModelBridge.agent_id == agent_id
        ).delete()

        ai_model = (
            db.session.query(AIModels)
            .filter(AIModels.model_name == agent_in.ai_model)
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
    if agent_in.language is not None:
        db.session.query(AgentLanguageBridge).filter(
            AgentLanguageBridge.agent_id == agent_id
        ).delete()

        language = (
            db.session.query(LanguageModel)
            .filter(LanguageModel.lang_code == agent_in.language)
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

    # ---- Knowledge Base Update ----
    if agent_in.knowledgebase is not None:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
        kb_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order
        
        # 2. Fetch from DB
        kb_records = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id.in_(kb_ids_ordered),
            KnowledgeBaseModel.user_id == current_user.id,
            KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
        ).all()
        
        # 3. Create a map for O(1) lookup
        kb_map = {kb.id: kb for kb in kb_records}

        # 4. Validate all IDs exist checking against the unique set of requested IDs
        found_ids = set(kb_map.keys())
        missing_ids = set(kb_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Knowledge Base IDs not found or not synced: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        el_kb_list = []
        for kb_id in kb_ids_ordered:
            kb = kb_map[kb_id]
            el_kb_list.append({
                "id": kb.elevenlabs_document_id,
                "type": "file",
                "name": kb.title or f"KB_{kb.id}"
            })
        
        el_update_params["knowledge_base"] = el_kb_list

        # Update DB bridge (delete old, add new)
        db.session.query(AgentKnowledgeBaseBridge).filter(
            AgentKnowledgeBaseBridge.agent_id == agent_id
        ).delete()
        for kb_id in kb_ids_ordered:
            db.session.add(AgentKnowledgeBaseBridge(agent_id=agent_id, kb_id=kb_id))

    # ---- Tools Update ----
    if agent_in.tools is not None:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
        tool_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order

        # 2. Fetch from DB
        tool_records = db.session.query(FunctionModel).filter(
            FunctionModel.id.in_(tool_ids_ordered),
            FunctionModel.elevenlabs_tool_id.isnot(None),
            or_(
                FunctionModel.user_id == current_user.id,
                FunctionModel.user_id.is_(None)
            )
        ).all()
        
        # 3. Create a map for O(1) lookup
        tool_map = {tool.id: tool for tool in tool_records}

        # 4. Validate all IDs exist
        found_ids = set(tool_map.keys())
        missing_ids = set(tool_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Tool IDs not found or not synced or not accessible to you: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        el_tool_ids = []
        for tool_id in tool_ids_ordered:
            tool = tool_map[tool_id]
            el_tool_ids.append(tool.elevenlabs_tool_id)

        el_update_params["tool_ids"] = el_tool_ids

        # Update DB bridge
        db.session.query(AgentFunctionBridgeModel).filter(
            AgentFunctionBridgeModel.agent_id == agent_id
        ).delete()
        for tool_id in tool_ids_ordered:
            db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=tool_id))

    # ---- Variables Update ----
    if agent_in.variables is not None:
        el_update_params["dynamic_variables"] = agent_in.variables
        
        # Update DB variables
        db.session.query(VariablesModel).filter(
            VariablesModel.agent_id == agent_id
        ).delete()
        for key, value in agent_in.variables.items():
            db.session.add(VariablesModel(agent_id=agent_id, variable_name=key, variable_value=value))

    # ---- Builtin Tools Update ----
    if agent_in.built_in_tools is not None:
        agent.built_in_tools = agent_in.built_in_tools.model_dump()
        el_update_params["built_in_tools"] = transform_built_in_tools(agent_in.built_in_tools, db.session, current_user.id)

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
            db.session.rollback()
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
        db.session.flush()  # Use flush instead of commit to allow rollback if ElevenLabs fails

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