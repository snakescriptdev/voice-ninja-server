# ... (Keep previous imports and helpers)
# I'll just rewrite and expand the whole file content to be sure.
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session, selectinload
from fastapi_sqlalchemy import db
from sqlalchemy import or_
from typing import List
import math
import uuid
import os
import shutil
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
    AgentLanguageBridge,
    AgentKnowledgeBaseBridge,
    AgentFunctionBridgeModel,
    FunctionApiConfig
)
from app_v2.schemas.function_schema import (
    FunctionCreateSchema,
    FunctionUpdateSchema,
    FunctionRead,
    ApiSchema,
    FunctionBind,
    FunctionUnbind,
    PrimitiveField
)
from app_v2.schemas.agent_schema import AgentCreate, AgentRead, AgentUpdate
from app_v2.schemas.web_agent_schema import WebAgentConfig, WebAgentConfigResponse, WebAgentConfigUpdate, WebAgentListResponse
from app_v2.schemas.language_schema import LanguageRead
from app_v2.schemas.voice_schema import VoiceRead
from app_v2.schemas.ai_model import AIModelRead
from app_v2.schemas.knowledge_base_schema import (
    KnowledgeBaseResponse, 
    KnowledgeBaseURLCreate, 
    KnowledgeBaseTextCreate, 
    KnowledgeBaseBind
)
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.schemas.enum_types import PhoneNumberAssignStatus, GenderEnum, RequestMethodEnum, PlanFeatureEnum
from app_v2.utils.public_auth import get_public_api_user
from app_v2.utils.crypto_utils import encrypt_data, decrypt_data
from app_v2.schemas.pagination import PaginatedResponse
from app_v2.utils.rate_limit import track_and_limit_api, log_public_api_call
from app_v2.utils.feature_access import RequireFeaturePublic
from app_v2.utils.elevenlabs.agent_utils import ElevenLabsAgent
from app_v2.utils.elevenlabs import ElevenLabsKB
from app_v2.utils.scraping_utils import scrape_webpage_title
from app_v2.utils.activity_logger import log_activity
from app_v2.core.logger import setup_logger
from fastapi import UploadFile, File, Form
import time
from app_v2.schemas.api_analytics_schema import APIAnalyticsResponse, APICallLogRead
from sqlalchemy import func
from datetime import timedelta

logger = setup_logger(__name__)

from fastapi.routing import APIRoute
from typing import Callable
from fastapi.responses import Response

class PublicAPIRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()
        async def custom(request: Request) -> Response:
            start_time = time.time()
            status_code = 500
            try:
                response = await original(request)
                status_code = response.status_code
                return response
            except HTTPException as e:
                status_code = e.status_code
                raise e
            except Exception as e:
                raise e
            finally:
                process_time_ms = int((time.time() - start_time) * 1000)
                client_id = request.headers.get("X-API-Client-ID")
                if client_id:
                    try:
                        with db():
                            from app_v2.databases.models import APIKeyModel
                            key = db.session.query(APIKeyModel).filter_by(client_id=client_id, is_active=True).first()
                            if key:
                                log_public_api_call(key.user_id, request.url.path, status_code, process_time_ms, 0)
                    except Exception as e:
                        logger.error(f"Failed to log public API call in route handler: {e}")

        return custom

router = APIRouter(
    prefix="/api/v2/public",
    tags=["public-api"],
    dependencies=[Depends(get_public_api_user), Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))],
    route_class=PublicAPIRoute
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

def voice_to_read(voice: VoiceModel) -> VoiceRead:
    gender = GenderEnum.male
    nationality = "british"
    
    if voice.traits:
        gender = voice.traits.gender.value if hasattr(voice.traits.gender, 'value') else str(voice.traits.gender)
        nationality = voice.traits.nationality

    return VoiceRead(
        id=voice.id,
        voice_name=voice.voice_name,
        is_custom_voice=voice.is_custom_voice,
        elevenlabs_voice_id=voice.elevenlabs_voice_id,
        gender=gender,
        nationality=nationality,
        has_sample_audio=voice.has_sample_audio,
        sample_audio_url=voice.audio_file
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

        # -------------------------------------------------
        # KB & Tools validation and lookup
        # -------------------------------------------------
        el_kb_list = []
        kb_ids_ordered = []
        if agent_in.knowledgebase:
            raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
            kb_ids_ordered = list(dict.fromkeys(raw_ids))
            kb_records = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id.in_(kb_ids_ordered),
                KnowledgeBaseModel.user_id == user_id,
                KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
            ).all()
            kb_map = {kb.id: kb for kb in kb_records}
            missing_ids = set(kb_ids_ordered) - set(kb_map.keys())
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Some Knowledge Base IDs not found or synced: {list(missing_ids)}")
            for kb_id in kb_ids_ordered:
                kb = kb_map[kb_id]
                el_kb_list.append({
                    "id": kb.elevenlabs_document_id,
                    "type": "file",
                    "name": kb.title or f"KB_{kb.id}"
                })

        el_tool_ids = []
        tool_ids_ordered = []
        if agent_in.tools:
            raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
            tool_ids_ordered = list(dict.fromkeys(raw_ids))
            tool_records = db.session.query(FunctionModel).filter(
                FunctionModel.id.in_(tool_ids_ordered),
                FunctionModel.elevenlabs_tool_id.isnot(None),
                or_(FunctionModel.user_id == user_id, FunctionModel.user_id.is_(None))
            ).all()
            tool_map = {tool.id: tool for tool in tool_records}
            missing_ids = set(tool_ids_ordered) - set(tool_map.keys())
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Some Tool IDs not found or accessible: {list(missing_ids)}")
            for tool_id in tool_ids_ordered:
                el_tool_ids.append(tool_map[tool_id].elevenlabs_tool_id)

        from app_v2.routers.agents import transform_built_in_tools
        transformed_built_in = transform_built_in_tools(agent_in.built_in_tools, db.session, user_id)

        # Create in ElevenLabs
        el_client = ElevenLabsAgent()
        el_response = el_client.create_agent(
            name=agent_in.agent_name,
            voice_id=voice.elevenlabs_voice_id,
            prompt=agent_in.system_prompt,
            first_message=agent_in.first_message or "Hello!",
            language=language.lang_code,
            llm_model=ai_model.model_name,
            tool_ids=el_tool_ids,
            knowledge_base=el_kb_list,
            dynamic_variables=agent_in.variables,
            built_in_tools=transformed_built_in
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
            built_in_tools=agent_in.built_in_tools.model_dump() if agent_in.built_in_tools else {}
        )
        db.session.add(new_agent)
        db.session.flush()
        
        db.session.add(AgentAIModelBridge(agent_id=new_agent.id, ai_model_id=ai_model.id))
        db.session.add(AgentLanguageBridge(agent_id=new_agent.id, lang_id=language.id))
        
        for kb_id in kb_ids_ordered:
            db.session.add(AgentKnowledgeBaseBridge(agent_id=new_agent.id, kb_id=kb_id))
        for tool_id in tool_ids_ordered:
            db.session.add(AgentFunctionBridgeModel(agent_id=new_agent.id, function_id=tool_id))
            
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
        
        el_update_params = {}
        
        # Simplified update: name, prompt, first_message
        if agent_in.agent_name is not None:
            agent.agent_name = agent_in.agent_name
            el_update_params["name"] = agent_in.agent_name
        if agent_in.system_prompt is not None:
            agent.system_prompt = agent_in.system_prompt
            el_update_params["prompt"] = agent_in.system_prompt
        if agent_in.first_message is not None:
            agent.first_message = agent_in.first_message
            el_update_params["first_message"] = agent_in.first_message

        # ---- Knowledge Base Update ----
        if agent_in.knowledgebase is not None:
            raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
            kb_ids_ordered = list(dict.fromkeys(raw_ids))
            
            kb_records = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.id.in_(kb_ids_ordered),
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
            ).all()
            
            kb_map = {kb.id: kb for kb in kb_records}
            missing_ids = set(kb_ids_ordered) - set(kb_map.keys())
            
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Some Knowledge Base IDs not found or not synced: {list(missing_ids)}")
            
            el_kb_list = []
            for kb_id in kb_ids_ordered:
                kb = kb_map[kb_id]
                el_kb_list.append({
                    "id": kb.elevenlabs_document_id,
                    "type": "file",
                    "name": kb.title or f"KB_{kb.id}"
                })
            
            el_update_params["knowledge_base"] = el_kb_list

            db.session.query(AgentKnowledgeBaseBridge).filter(
                AgentKnowledgeBaseBridge.agent_id == agent_id
            ).delete()
            for kb_id in kb_ids_ordered:
                db.session.add(AgentKnowledgeBaseBridge(agent_id=agent_id, kb_id=kb_id))

        # ---- Tools Update ----
        if agent_in.tools is not None:
            raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
            tool_ids_ordered = list(dict.fromkeys(raw_ids))

            tool_records = db.session.query(FunctionModel).filter(
                FunctionModel.id.in_(tool_ids_ordered),
                FunctionModel.elevenlabs_tool_id.isnot(None),
                or_(
                    FunctionModel.user_id == current_user.id,
                    FunctionModel.user_id.is_(None)
                )
            ).all()
            
            tool_map = {tool.id: tool for tool in tool_records}
            missing_ids = set(tool_ids_ordered) - set(tool_map.keys())
            
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Some Tool IDs not found or synced: {list(missing_ids)}")
            
            el_tool_ids = []
            for tool_id in tool_ids_ordered:
                el_tool_ids.append(tool_map[tool_id].elevenlabs_tool_id)

            el_update_params["tool_ids"] = el_tool_ids

            db.session.query(AgentFunctionBridgeModel).filter(
                AgentFunctionBridgeModel.agent_id == agent_id
            ).delete()
            for tool_id in tool_ids_ordered:
                db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=tool_id))

        # ---- Built-in Tools Update ----
        if agent_in.built_in_tools is not None:
            agent.built_in_tools = agent_in.built_in_tools.model_dump()
            from app_v2.routers.agents import transform_built_in_tools
            el_update_params["built_in_tools"] = transform_built_in_tools(agent_in.built_in_tools, db.session, current_user.id)

        # ---- Variables Update ----
        if agent_in.variables is not None:
            el_update_params["dynamic_variables"] = agent_in.variables
            
            db.session.query(VariablesModel).filter(
                VariablesModel.agent_id == agent_id
            ).delete()
            for key, value in agent_in.variables.items():
                db.session.add(VariablesModel(agent_id=agent_id, variable_name=key, variable_value=value))

        # ---- Sync with ElevenLabs ----
        if el_update_params and agent.elevenlabs_agent_id:
            try:
                el_client = ElevenLabsAgent()
                el_response = el_client.update_agent(
                    agent_id=agent.elevenlabs_agent_id,
                    **el_update_params
                )
                if not el_response.status:
                    db.session.rollback()
                    raise HTTPException(
                        status_code=424,
                        detail=f"Failed to update agent in ElevenLabs: {el_response.error_message}"
                    )
            except HTTPException:
                raise
            except Exception as e:
                db.session.rollback()
                raise HTTPException(
                    status_code=424,
                    detail=f"Failed to update agent in ElevenLabs due to an unexpected error: {str(e)}"
                )

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

# -------------------------------------------------------------------
# LANGUAGES
# -------------------------------------------------------------------

@router.get("/languages", response_model=PaginatedResponse[LanguageRead])
async def list_languages_public(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    skip = (max(1, page) - 1) * size
    with db():
        query = db.session.query(LanguageModel)
        total = query.count()
        languages = query.order_by(LanguageModel.id.asc()).offset(skip).limit(size).all()
        return PaginatedResponse(total=total, page=page, size=size, pages=math.ceil(total/size), items=languages)

@router.get("/languages/{id}", response_model=LanguageRead)
async def get_language_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        language = db.session.query(LanguageModel).filter(LanguageModel.id == id).first()
        if not language:
            raise HTTPException(status_code=404, detail="Language not found")
        return language

# -------------------------------------------------------------------
# VOICES
# -------------------------------------------------------------------

@router.get("/voices", response_model=PaginatedResponse[VoiceRead])
async def list_voices_public(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    skip = (max(1, page) - 1) * size
    with db():
        filters = [
            or_(
                VoiceModel.user_id == current_user.id,
                VoiceModel.user_id.is_(None),
            ),
        ]
            
        query = db.session.query(VoiceModel).options(selectinload(VoiceModel.traits)).filter(*filters)
        total = query.count()
        voices = query.order_by(VoiceModel.id.asc()).offset(skip).limit(size).all()
        items = [voice_to_read(v) for v in voices]
        return PaginatedResponse(total=total, page=page, size=size, pages=math.ceil(total/size), items=items)

@router.get("/voices/{id}", response_model=VoiceRead)
async def get_voice_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        voice = db.session.query(VoiceModel).options(selectinload(VoiceModel.traits)).filter(
            VoiceModel.id == id,
            or_(
                VoiceModel.user_id == current_user.id,
                VoiceModel.user_id.is_(None)
            )
        ).first()
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        return voice_to_read(voice)

# -------------------------------------------------------------------
# AI MODELS
# -------------------------------------------------------------------

@router.get("/ai-models", response_model=PaginatedResponse[AIModelRead])
async def list_ai_models_public(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    skip = (max(1, page) - 1) * size
    with db():
        query = db.session.query(AIModels)
        total = query.count()
        models = query.order_by(AIModels.model_name.asc()).offset(skip).limit(size).all()
        return PaginatedResponse(total=total, page=page, size=size, pages=math.ceil(total/size), items=models)
# -------------------------------------------------------------------
# KNOWLEDGE BASE
# -------------------------------------------------------------------

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt"}

def sync_agent_kb_logic(agent_id: int):
    """Internal helper for KB syncing with ElevenLabs"""
    try:
        with db():
            agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
            if not agent or not agent.elevenlabs_agent_id:
                return

            all_kb = (
                db.session.query(KnowledgeBaseModel)
                .join(AgentKnowledgeBaseBridge)
                .filter(AgentKnowledgeBaseBridge.agent_id == agent_id, KnowledgeBaseModel.elevenlabs_document_id.isnot(None))
                .all()
            )

            kb_docs = []
            for item in all_kb:
                doc_type = "file" if item.kb_type == "file" else "url" if item.kb_type == "url" else "text"
                kb_docs.append({
                    "id": item.elevenlabs_document_id,
                    "name": item.title or "Untitled",
                    "type": doc_type,
                    "usage_mode": "auto"
                })

            agent_client = ElevenLabsAgent()
            agent_client.update_agent(
                agent_id=agent.elevenlabs_agent_id,
                knowledge_base=kb_docs
            )
    except Exception as e:
        logger.error(f"Failed to sync KB for agent {agent_id}: {e}")

@router.get("/kb", response_model=PaginatedResponse[KnowledgeBaseResponse])
async def list_kb_public(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    skip = (max(1, page) - 1) * size
    with db():
        query = db.session.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.user_id == current_user.id)
        total = query.count()
        kb_items = query.order_by(KnowledgeBaseModel.id.asc()).offset(skip).limit(size).all()
        return PaginatedResponse(total=total, page=page, size=size, pages=math.ceil(total/size), items=kb_items)

@router.get("/kb/{id}", response_model=KnowledgeBaseResponse)
async def get_kb_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        kb_item = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id == id, KnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_item:
            raise HTTPException(status_code=404, detail="Knowledge Base item not found")
        return kb_item

@router.post("/kb/url", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_kb_url_public(
    request: KnowledgeBaseURLCreate,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    url_str = str(request.url)
    with db():
        kb_client = ElevenLabsKB()
        kb_response = kb_client.add_url_document(url_str)
        if not kb_response.status:
            raise HTTPException(status_code=424, detail=f"ElevenLabs failure: {kb_response.error_message}")
        
        doc_id = kb_response.data.get("document_id")
        rag_id = kb_client.compute_rag_index(doc_id)
        title = scrape_webpage_title(url_str)

        kb_entry = KnowledgeBaseModel(
            user_id=current_user.id,
            kb_type="url",
            content_path=url_str,
            elevenlabs_document_id=doc_id,
            rag_index_id=rag_id,
            title=title
        )
        db.session.add(kb_entry)
        db.session.commit()
        db.session.refresh(kb_entry)
        return kb_entry

@router.post("/kb/text", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_kb_text_public(
    request: KnowledgeBaseTextCreate,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        kb_client = ElevenLabsKB()
        kb_response = kb_client.add_text_document(request.content, request.title)
        if not kb_response.status:
            raise HTTPException(status_code=424, detail=f"ElevenLabs failure: {kb_response.error_message}")
        
        doc_id = kb_response.data.get("document_id")
        rag_id = kb_client.compute_rag_index(doc_id)

        kb_entry = KnowledgeBaseModel(
            user_id=current_user.id,
            kb_type="text",
            title=request.title,
            content_text=request.content,
            elevenlabs_document_id=doc_id,
            rag_index_id=rag_id
        )
        db.session.add(kb_entry)
        db.session.commit()
        db.session.refresh(kb_entry)
        return kb_entry

@router.post("/kb/file", response_model=List[KnowledgeBaseResponse], status_code=status.HTTP_201_CREATED)
async def create_kb_file_public(
    files: List[UploadFile] = File(...),
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    responses = []
    
    with db():
        kb_client = ElevenLabsKB()
        for file in files:
            _, ext = os.path.splitext(file.filename)
            if ext.lower() not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds 10MB limit")

            file_path = os.path.join(UPLOAD_DIR, f"pub_{current_user.id}_{datetime.now().timestamp()}_{file.filename}")
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            kb_response = kb_client.upload_document(file_path, name=file.filename)
            if not kb_response.status:
                if os.path.exists(file_path): os.remove(file_path)
                raise HTTPException(status_code=424, detail=f"ElevenLabs failure for {file.filename}: {kb_response.error_message}")
            
            doc_id = kb_response.data.get("document_id")
            rag_id = kb_client.compute_rag_index(doc_id)

            kb_entry = KnowledgeBaseModel(
                user_id=current_user.id,
                kb_type="file",
                title=file.filename,
                content_path=file_path,
                elevenlabs_document_id=doc_id,
                rag_index_id=rag_id,
                file_size=round((file_size / (1024*1024)), 2)
            )
            db.session.add(kb_entry)
            db.session.flush()
            
            # Reattach to session strictly just to be sure
            responses.append(kb_entry)

        db.session.commit()
        for entry in responses:
            db.session.refresh(entry)
            
        return responses

@router.delete("/kb/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        kb_entry = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id == id, KnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_entry:
            raise HTTPException(status_code=404, detail="Knowledge Base item not found")
        
        bridges = db.session.query(AgentKnowledgeBaseBridge).filter(AgentKnowledgeBaseBridge.kb_id == id).all()
        agent_ids = [b.agent_id for b in bridges]

        if kb_entry.elevenlabs_document_id:
            try:
                ElevenLabsKB().delete_document(kb_entry.elevenlabs_document_id)
            except: pass

        if kb_entry.kb_type == "file" and kb_entry.content_path and os.path.exists(kb_entry.content_path):
            try: os.remove(kb_entry.content_path)
            except: pass

        for bridge in bridges: db.session.delete(bridge)
        db.session.delete(kb_entry)
        db.session.commit()

        for agent_id in agent_ids: sync_agent_kb_logic(agent_id)
    return None

@router.post("/kb/bind", status_code=status.HTTP_200_OK)
async def bind_kb_public(
    request: KnowledgeBaseBind,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == request.agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        
        kb_entry = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id == request.kb_id, KnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_entry: raise HTTPException(status_code=404, detail="Knowledge Base item not found")
        
        existing = db.session.query(AgentKnowledgeBaseBridge).filter(
            AgentKnowledgeBaseBridge.agent_id == request.agent_id, AgentKnowledgeBaseBridge.kb_id == request.kb_id
        ).first()
        
        if not existing:
            db.session.add(AgentKnowledgeBaseBridge(agent_id=request.agent_id, kb_id=request.kb_id))
            db.session.commit()
            sync_agent_kb_logic(request.agent_id)

    return {"message": "Knowledge base bound successfully"}

@router.get("/ai-models/{id}", response_model=AIModelRead)
async def get_ai_model_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        model = db.session.query(AIModels).filter(AIModels.id == id).first()
        if not model:
            raise HTTPException(status_code=404, detail="AI Model not found")
        return model

# -------------------------------------------------------------------
# FUNCTIONS (TOOLS) CRUD
# -------------------------------------------------------------------

# -------------------------------------------------------------------
# FUNCTIONS (TOOLS) CRUD
# -------------------------------------------------------------------

SENSITIVE_HEADER_KEYS = {"authorization", "x-api-key", "api-key", "token"}

def function_to_read(f: FunctionModel) -> FunctionRead:
    db_config = f.api_endpoint_url
    if not db_config:
        raise HTTPException(status_code=500, detail=f"Function '{f.name}' has no API config")

    decrypted_headers = {}
    for k, v in (db_config.headers or {}).items():
        if k.lower() in SENSITIVE_HEADER_KEYS:
            try:
                decrypted_headers[k] = decrypt_data(v)
            except Exception:
                decrypted_headers[k] = v
        else:
            decrypted_headers[k] = v

    return FunctionRead(
        id=f.id,
        name=f.name,
        description=f.description,
        elevenlabs_tool_id=f.elevenlabs_tool_id,
        created_at=f.created_at,
        modified_at=f.modified_at,
        api_config=ApiSchema(
            url=db_config.endpoint_url,
            method=db_config.http_method,
            request_headers=decrypted_headers,
            path_params_schema={k: PrimitiveField(**v) for k, v in db_config.path_params.items()} if db_config.path_params else None,
            query_params_schema=db_config.query_params if db_config.query_params else None,
            request_body_schema=db_config.body_schema if db_config.body_schema else None,
            response_variables=db_config.response_variables if db_config.response_variables else None,
            content_type="application/json" if db_config.body_schema else None,
        )
    )


@router.get("/functions", response_model=PaginatedResponse[FunctionRead])
async def list_functions_public(
    page: int = 1,
    size: int = 20,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    skip = (max(1, page) - 1) * size
    with db():
        query = db.session.query(FunctionModel).filter(FunctionModel.user_id == current_user.id)
        total = query.count()
        functions = (
            query
            .options(selectinload(FunctionModel.api_endpoint_url))
            .order_by(FunctionModel.created_at.desc())
            .offset(skip).limit(size).all()
        )
        items = [function_to_read(f) for f in functions]
        return PaginatedResponse(total=total, page=page, size=size, pages=math.ceil(total/size), items=items)


@router.get("/functions/{id}", response_model=FunctionRead)
async def get_function_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        function = (
            db.session.query(FunctionModel)
            .options(selectinload(FunctionModel.api_endpoint_url))
            .filter(FunctionModel.id == id, FunctionModel.user_id == current_user.id)
            .first()
        )
        if not function:
            raise HTTPException(status_code=404, detail="Function not found")
        return function_to_read(function)


@router.post("/functions", response_model=FunctionRead, status_code=status.HTTP_201_CREATED)
async def create_function_public(
    function_in: FunctionCreateSchema,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    user_id = current_user.id

    with db():
        existing = db.session.query(FunctionModel).filter(
            FunctionModel.name == function_in.name,
            FunctionModel.user_id == user_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Function with name '{function_in.name}' already exists")

        el_client = ElevenLabsAgent()
        el_response = el_client.create_tool(
            name=function_in.name,
            description=function_in.description,
            api_schema=function_in.api_config
        )
        if not el_response.status:
            raise HTTPException(status_code=424, detail=f"ElevenLabs failure: {el_response.error_message}")

        elevenlabs_tool_id = el_response.data.get("id")

        try:
            new_function = FunctionModel(
                name=function_in.name,
                description=function_in.description,
                user_id=user_id,
                elevenlabs_tool_id=elevenlabs_tool_id
            )
            db.session.add(new_function)
            db.session.flush()

            headers = function_in.api_config.request_headers or {}
            encrypted_headers = {
                k: (encrypt_data(v) if k.lower() in SENSITIVE_HEADER_KEYS else v)
                for k, v in headers.items()
            }

            api_config = FunctionApiConfig(
                function_id=new_function.id,
                endpoint_url=function_in.api_config.url,
                http_method=function_in.api_config.method,
                headers=encrypted_headers,
                path_params={k: v.model_dump(exclude_none=True) for k, v in function_in.api_config.path_params_schema.items()} if function_in.api_config.path_params_schema else None,
                query_params=function_in.api_config.query_params_schema.model_dump(exclude_none=True) if function_in.api_config.query_params_schema else None,
                body_schema=function_in.api_config.request_body_schema.model_dump() if function_in.api_config.request_body_schema else None,
                response_variables=function_in.api_config.response_variables,
                timeout_ms=30000,
                speak_while_execution=False,
                speak_after_execution=True
            )
            db.session.add(api_config)
            db.session.commit()

            # re-fetch with api_endpoint_url eagerly loaded
            new_function = (
                db.session.query(FunctionModel)
                .options(selectinload(FunctionModel.api_endpoint_url))
                .filter(FunctionModel.id == new_function.id)
                .first()
            )
            return function_to_read(new_function)
        except HTTPException:
            raise
        except Exception as e:
            db.session.rollback()
            if elevenlabs_tool_id:
                el_client.delete_tool(elevenlabs_tool_id)
            raise HTTPException(status_code=500, detail=str(e))


@router.patch("/functions/{id}", response_model=FunctionRead)
async def update_function_public(
    id: int,
    function_in: FunctionUpdateSchema,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        function = (
            db.session.query(FunctionModel)
            .options(selectinload(FunctionModel.api_endpoint_url))
            .filter(FunctionModel.id == id, FunctionModel.user_id == current_user.id)
            .first()
        )
        if not function:
            raise HTTPException(status_code=404, detail="Function not found")

        el_update = False
        el_params = {}

        if function_in.name is not None:
            function.name = function_in.name
            el_params["name"] = function_in.name
            el_update = True

        if function_in.description is not None:
            function.description = function_in.description
            el_params["description"] = function_in.description
            el_update = True

        if function_in.api_config is not None or function_in.response_variables is not None:
            api_config = function.api_endpoint_url
            if not api_config:
                api_config = FunctionApiConfig(function_id=id)
                db.session.add(api_config)

            if function_in.response_variables is not None:
                api_config.response_variables = function_in.response_variables

            if function_in.api_config is not None:
                if function_in.api_config.url is not None:
                    api_config.endpoint_url = function_in.api_config.url
                if function_in.api_config.method is not None:
                    api_config.http_method = function_in.api_config.method
                if function_in.api_config.request_headers is not None:
                    api_config.headers = {
                        k: (encrypt_data(v) if k.lower() in SENSITIVE_HEADER_KEYS else v)
                        for k, v in function_in.api_config.request_headers.items()
                    }
                if function_in.api_config.path_params_schema is not None:
                    api_config.path_params = {k: v.model_dump(exclude_none=True) for k, v in function_in.api_config.path_params_schema.items()}
                if function_in.api_config.query_params_schema is not None:
                    api_config.query_params = function_in.api_config.query_params_schema.model_dump(exclude_none=True)
                if function_in.api_config.request_body_schema is not None:
                    api_config.body_schema = function_in.api_config.request_body_schema.model_dump()
                if function_in.api_config.response_variables is not None:
                    api_config.response_variables = function_in.api_config.response_variables

            decrypted_headers = {}
            for k, v in (api_config.headers or {}).items():
                if k.lower() in SENSITIVE_HEADER_KEYS:
                    try:
                        decrypted_headers[k] = decrypt_data(v)
                    except Exception:
                        decrypted_headers[k] = v
                else:
                    decrypted_headers[k] = v

            el_params["api_schema"] = ApiSchema(
                url=api_config.endpoint_url,
                method=api_config.http_method,
                request_headers=decrypted_headers,
                path_params_schema={k: PrimitiveField(**v) for k, v in api_config.path_params.items()} if api_config.path_params else None,
                query_params_schema=api_config.query_params if api_config.query_params else None,
                request_body_schema=api_config.body_schema if api_config.body_schema else None,
                response_variables=api_config.response_variables,
                content_type="application/json" if api_config.body_schema else None,
            )
            el_update = True

        if el_update and function.elevenlabs_tool_id:
            el_client = ElevenLabsAgent()
            el_res = el_client.update_tool(tool_id=function.elevenlabs_tool_id, **el_params)
            if not el_res.status:
                db.session.rollback()
                raise HTTPException(status_code=424, detail=f"ElevenLabs update failure: {el_res.error_message}")

        db.session.commit()
        db.session.refresh(function)
        return function_to_read(function)


@router.delete("/functions/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_function_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    with db():
        function = db.session.query(FunctionModel).filter(
            FunctionModel.id == id, FunctionModel.user_id == current_user.id
        ).first()
        if not function:
            raise HTTPException(status_code=404, detail="Function not found")

        if function.elevenlabs_tool_id:
            try:
                ElevenLabsAgent().delete_tool(function.elevenlabs_tool_id)
            except Exception:
                pass

        db.session.delete(function)
        db.session.commit()
    return None

@router.post("/functions/bind", status_code=status.HTTP_200_OK)
async def bind_function_public(
    request: FunctionBind,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    agent_id = request.agent_id
    function_id = request.function_id
    with db():
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.user_id == current_user.id).first()
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        
        function = db.session.query(FunctionModel).filter(FunctionModel.id == function_id, FunctionModel.user_id == current_user.id).first()
        if not function: raise HTTPException(status_code=404, detail="Function not found")
        
        existing = db.session.query(AgentFunctionBridgeModel).filter(
            AgentFunctionBridgeModel.agent_id == agent_id, AgentFunctionBridgeModel.function_id == function_id
        ).first()
        
        if not existing:
            db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=function_id))
            db.session.commit()
            # ElevenLabs Sync
            if agent.elevenlabs_agent_id:
                bridges = db.session.query(AgentFunctionBridgeModel).filter(AgentFunctionBridgeModel.agent_id == agent_id).all()
                tool_ids = [b.function.elevenlabs_tool_id for b in bridges if b.function.elevenlabs_tool_id]
                ElevenLabsAgent().update_agent(agent_id=agent.elevenlabs_agent_id, tool_ids=tool_ids)
                
    return {"message": "Function bound successfully"}

@router.post("/functions/unbind", status_code=status.HTTP_200_OK)
async def unbind_function_public(
    request: FunctionUnbind,
    current_user: UnifiedAuthModel = Depends(get_public_api_user)
):
    track_and_limit_api(current_user.id)
    agent_id = request.agent_id
    function_id = request.function_id
    with db():
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.user_id == current_user.id).first()
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        
        bridge = db.session.query(AgentFunctionBridgeModel).filter(
            AgentFunctionBridgeModel.agent_id == agent_id, AgentFunctionBridgeModel.function_id == function_id
        ).first()
        
        if bridge:
            db.session.delete(bridge)
            db.session.commit()
            # ElevenLabs Sync
            if agent.elevenlabs_agent_id:
                bridges = db.session.query(AgentFunctionBridgeModel).filter(AgentFunctionBridgeModel.agent_id == agent_id).all()
                tool_ids = [b.function.elevenlabs_tool_id for b in bridges if b.function.elevenlabs_tool_id]
                ElevenLabsAgent().update_agent(agent_id=agent.elevenlabs_agent_id, tool_ids=tool_ids)
                
    return {"message": "Function unbound successfully"}

