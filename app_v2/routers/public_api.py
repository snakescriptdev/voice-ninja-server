# ... (Keep previous imports and helpers)
# I'll just rewrite and expand the whole file content to be sure.
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session, selectinload
from fastapi_sqlalchemy import db
from typing import List
import math
import uuid
from datetime import datetime

from app_v2.databases.models import (
    AgentModel, 
    WebAgentModel, 
    UnifiedAuthModel,
    VoiceModel,
    AIModels,
    LanguageModel,
    PhoneNumberService,
    KnowledgeBaseModel,
    FunctionModel,
    VariablesModel,
    AgentAIModelBridge,
    AgentLanguageBridge,
    AgentKnowledgeBaseBridge,
    AgentFunctionBridgeModel
)
from app_v2.schemas.agent_schema import AgentCreate, AgentRead, AgentUpdate
from app_v2.schemas.web_agent_schema import WebAgentConfig, WebAgentConfigResponse, WebAgentConfigUpdate, WebAgentListResponse
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.enum_types import PhoneNumberAssignStatus
from app_v2.utils.public_auth import get_public_api_user
from app_v2.utils.rate_limit import track_and_limit_api
from app_v2.utils.elevenlabs.agent_utils import ElevenLabsAgent
from app_v2.utils.activity_logger import log_activity
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/public",
    tags=["public-api"],
    dependencies=[Depends(get_public_api_user)]
)

# -------------------- HELPERS --------------------

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
            {"id": bridge.knowledge_base.id, "title": bridge.knowledge_base.title, "type": bridge.knowledge_base.kb_type}
            for bridge in agent.agent_knowledge_bases
        ],
        variables={var.variable_name: var.variable_value for var in agent.variables},
        tools=[
            {"id": bridge.function.id, "name": bridge.function.name} 
            for bridge in agent.agent_functions
        ],
        built_in_tools=agent.built_in_tools
    )

def web_agent_to_response(web_agent: WebAgentModel, request: Request = None) -> WebAgentConfigResponse:
    base_url = str(request.base_url).rstrip("/") if request else ""
    return WebAgentConfigResponse(
        id=web_agent.id,
        public_id=web_agent.public_id,
        web_agent_name=web_agent.web_agent_name,
        shareable_link=f"{base_url}/api/v2/web-agent/preview/{web_agent.public_id}",
        agent_id=web_agent.agent_id,
        is_enabled=web_agent.is_enabled,
        appearance={
            "widget_title": web_agent.widget_title,
            "widget_subtitle": web_agent.widget_subtitle,
            "primary_color": web_agent.primary_color,
            "position": web_agent.position,
            "show_branding": web_agent.show_branding
        },
        prechat={
            "enable_prechat": web_agent.enable_prechat,
            "require_name": web_agent.require_name,
            "require_email": web_agent.require_email,
            "require_phone": web_agent.require_phone,
            "custom_fields": web_agent.custom_fields or []
        }
    )

# -------------------------------------------------------------------
# AGENTS CRUD
# -------------------------------------------------------------------

@router.get("/agents", response_model=PaginatedResponse[AgentRead])
async def list_agents(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    skip = (max(1, page) - 1) * size
    with db():
        query = db.session.query(AgentModel).filter(AgentModel.user_id == current_user.id)
        total = query.count()
        agents = query.order_by(AgentModel.created_at.desc()).offset(skip).limit(size).all()
        items = [agent_to_read(a) for a in agents]
        return PaginatedResponse(total=total, page=page, size=size, pages=math.ceil(total/size), items=items)

@router.get("/agents/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent_to_read(agent)

@router.post("/agents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    agent_in: AgentCreate,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    # Reusing original creation logic from agents.py would be ideal, but for public API 
    # we'll implement it here to ensure it uses the public auth and tracking.
    # (Implementation logic would be identical to agents.create_agent but restricted to the specific user)
    # Since it's a lot of code, I'll refer to original implementation and adapt.
    
    user_id = current_user.id
    with db():
        voice = db.session.query(VoiceModel).filter(VoiceModel.voice_name == agent_in.voice).first()
        if not voice or not voice.elevenlabs_voice_id:
            raise HTTPException(status_code=400, detail="Invalid voice")
        
        ai_model = db.session.query(AIModels).filter(AIModels.model_name == agent_in.ai_model).first()
        if not ai_model:
            raise HTTPException(status_code=400, detail="Invalid AI model")
        
        language = db.session.query(LanguageModel).filter(LanguageModel.lang_code == agent_in.language).first()
        if not language:
            raise HTTPException(status_code=400, detail="Invalid language")

        # Create in ElevenLabs
        el_client = ElevenLabsAgent()
        el_response = el_client.create_agent(
            name=agent_in.agent_name,
            voice_id=voice.elevenlabs_voice_id,
            prompt=agent_in.system_prompt,
            first_message=agent_in.first_message or "Hello!",
            language=language.lang_code,
            llm_model=ai_model.model_name,
            dynamic_variables=agent_in.variables
        )
        if not el_response.status:
            raise HTTPException(status_code=424, detail="ElevenLabs failure")
        
        elevenlabs_agent_id = el_response.data.get("agent_id")
        
        new_agent = AgentModel(
            agent_name=agent_in.agent_name,
            first_message=agent_in.first_message,
            system_prompt=agent_in.system_prompt,
            user_id=user_id,
            agent_voice=voice.id,
            elevenlabs_agent_id=elevenlabs_agent_id,
            built_in_tools={}
        )
        db.session.add(new_agent)
        db.session.flush()
        db.session.add(AgentAIModelBridge(agent_id=new_agent.id, ai_model_id=ai_model.id))
        db.session.add(AgentLanguageBridge(agent_id=new_agent.id, lang_id=language.id))
        db.session.commit()
        db.session.refresh(new_agent)
        return agent_to_read(new_agent)

@router.put("/agents/{agent_id}", response_model=AgentRead)
async def update_agent_public(
    agent_id: int,
    agent_in: AgentUpdate,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        # Simplified update: name, prompt, first_message
        if agent_in.agent_name: agent.agent_name = agent_in.agent_name
        if agent_in.system_prompt: agent.system_prompt = agent_in.system_prompt
        if agent_in.first_message: agent.first_message = agent_in.first_message
        
        db.session.commit()
        db.session.refresh(agent)
        return agent_to_read(agent)

@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_public(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        if agent.elevenlabs_agent_id:
            ElevenLabsAgent().delete_agent(agent.elevenlabs_agent_id)
            
        db.session.delete(agent)
        db.session.commit()
    return None

# -------------------------------------------------------------------
# WEB AGENTS CRUD
# -------------------------------------------------------------------

@router.get("/web-agents", response_model=PaginatedResponse[WebAgentListResponse])
async def list_web_agents(
    request: Request,
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    skip = (max(1, page) - 1) * size
    base_url = str(request.base_url).rstrip("/")
    with db():
        query = db.session.query(WebAgentModel).filter(WebAgentModel.user_id == current_user.id)
        total = query.count()
        web_agents = query.order_by(WebAgentModel.created_at.desc()).offset(skip).limit(size).all()
        
        items = [
            WebAgentListResponse(
                id=wa.id,
                web_agent_name=wa.web_agent_name,
                public_id=wa.public_id,
                shareable_link=f"{base_url}/api/v2/web-agent/preview/{wa.public_id}",
                is_enabled=wa.is_enabled,
                created_at=wa.created_at,
                agent_name=wa.agent.agent_name
            ) for wa in web_agents
        ]
        return PaginatedResponse(total=total, page=page, size=size, pages=math.ceil(total/size), items=items)

@router.get("/web-agents/{public_id}", response_model=WebAgentConfigResponse)
async def get_web_agent(
    public_id: str,
    request: Request,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        wa = db.session.query(WebAgentModel).filter(
            WebAgentModel.public_id == public_id, WebAgentModel.user_id == current_user.id
        ).first()
        if not wa:
            raise HTTPException(status_code=404, detail="Web Agent not found")
        return web_agent_to_response(wa, request)

@router.post("/web-agents", response_model=WebAgentConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_web_agent(
    wa_in: WebAgentConfig,
    request: Request,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == wa_in.agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        new_wa = WebAgentModel(
            user_id=current_user.id,
            agent_id=wa_in.agent_id,
            web_agent_name=wa_in.web_agent_name,
            widget_title=wa_in.appearance.widget_title,
            widget_subtitle=wa_in.appearance.widget_subtitle,
            primary_color=wa_in.appearance.primary_color,
            position=wa_in.appearance.position,
            show_branding=wa_in.appearance.show_branding,
            enable_prechat=wa_in.prechat.enable_prechat,
            require_name=wa_in.prechat.require_name,
            require_email=wa_in.prechat.require_email,
            require_phone=wa_in.prechat.require_phone,
            custom_fields=[f.model_dump() for f in wa_in.prechat.custom_fields]
        )
        db.session.add(new_wa)
        db.session.commit()
        db.session.refresh(new_wa)
        return web_agent_to_response(new_wa, request)

@router.put("/web-agents/{public_id}", response_model=WebAgentConfigResponse)
async def update_web_agent(
    public_id: str,
    wa_in: WebAgentConfigUpdate,
    request: Request,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        wa = db.session.query(WebAgentModel).filter(
            WebAgentModel.public_id == public_id, WebAgentModel.user_id == current_user.id
        ).first()
        if not wa:
            raise HTTPException(status_code=404, detail="Web Agent not found")
        
        if wa_in.web_agent_name: wa.web_agent_name = wa_in.web_agent_name
        if wa_in.is_enabled is not None: wa.is_enabled = wa_in.is_enabled
        if wa_in.appearance:
            if wa_in.appearance.widget_title: wa.widget_title = wa_in.appearance.widget_title
            if wa_in.appearance.widget_subtitle: wa.widget_subtitle = wa_in.appearance.widget_subtitle
            if wa_in.appearance.primary_color: wa.primary_color = wa_in.appearance.primary_color
            if wa_in.appearance.position: wa.position = wa_in.appearance.position
            if wa_in.appearance.show_branding is not None: wa.show_branding = wa_in.appearance.show_branding
        
        db.session.commit()
        db.session.refresh(wa)
        return web_agent_to_response(wa, request)

@router.delete("/web-agents/{public_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_web_agent(
    public_id: str,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        wa = db.session.query(WebAgentModel).filter(
            WebAgentModel.public_id == public_id, WebAgentModel.user_id == current_user.id
        ).first()
        if not wa:
            raise HTTPException(status_code=404, detail="Web Agent not found")
        db.session.delete(wa)
        db.session.commit()
    return None

# ... (I'll implement POST/PUT/DELETE later or in a larger chunk if possible)
# For brevity and safety, let's include the rest of them.
