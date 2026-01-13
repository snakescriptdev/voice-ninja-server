from fastapi import APIRouter,Request,UploadFile, Form, Depends
from fastapi.templating import Jinja2Templates
from elevenlabs_app.core import VoiceSettings
from elevenlabs_app.core.config import DEFAULT_VARS,NOISE_SETTINGS_DESCRIPTIONS
from elevenlabs_app.utils.helper import Paginator, check_session_expiry_redirect,update_system_variables
from fastapi.responses import RedirectResponse, FileResponse, Response, HTMLResponse,JSONResponse
from app.databases.models import AgentModel, KnowledgeBaseModel, agent_knowledge_association, UserModel, AgentConnectionModel, CustomFunctionModel, ApprovedDomainModel, DailyCallLimitModel, OverallTokenLimitModel,VoiceModel,LLMModel,ElevenLabModel,SystemVariable
from sqlalchemy.orm import sessionmaker
from app.databases.models import engine
import os, shutil
from dotenv import load_dotenv
from fastapi import Query
from app.databases.models import ElevenLabsWebhookToolModel

from sqlalchemy.exc import SQLAlchemyError
from app.routers.schemas.voice_schemas import (
    validate_create_voice_request,
    validate_edit_voice,
    validate_delete_voice,
)
from app.services.elevenlabs_utils import ElevenLabsUtils
from elevenlabs_app.elevenlabs_config import DEFAULT_LANGUAGE,DEFAULT_LLM_ELEVENLAB,DEFAULT_MODEL_ELEVENLAB,ELEVENLABS_MODELS,VALID_LLMS
from sqlalchemy import select, insert, delete

load_dotenv()
ElevenLabsWebRouter = APIRouter()
templates = Jinja2Templates(directory="templates/")

@ElevenLabsWebRouter.get("/update_agent")
@check_session_expiry_redirect
async def update_agent(request: Request):
    try:
        agent_id = request.query_params.get("agent_id")
        user_id = request.session.get("user", {}).get("user_id")

        if not agent_id or not user_id:
            return {"error": "Missing agent_id or user session"}
        
        Session = sessionmaker(bind=engine)
        session = Session()

        system_var_query_result = session.execute(select(SystemVariable))
        system_variables = system_var_query_result.scalars().all()
        system_variables_data = [
                {
                    "id": sv.id,
                    "name": sv.name,
                    "value" : None,
                    "description": sv.description,
                }
                for sv in system_variables
            ]
        

        # Convert agent_id to integer for database queries
        try:
            agent_id_int = int(agent_id)
        except ValueError:
            return {"error": "Invalid agent_id format"}
        
        # Fetch agent knowledge associations
        result = session.execute(
            select(agent_knowledge_association).where(agent_knowledge_association.c.agent_id == agent_id_int)
        )
        agent_knowledge_ids = [(row.agent_id, row.knowledge_base_id) for row in result.fetchall()]

        # Fetch agent details
        agent_result =  session.execute(select(AgentModel).where(AgentModel.id == agent_id_int))
        agent = agent_result.scalars().first()

        try:
            system_variables_data = update_system_variables(system_variables_data, agent)
        except Exception as ex:
            pass

        # Fetch all knowledge bases
        knowledge_result =  session.execute(select(KnowledgeBaseModel))
        knowledge_bases = knowledge_result.scalars().all()
        dynamic_variables = agent.dynamic_variable

        noise_settings_variables = agent.noise_setting_variable or {}
        merged_noise_vars = {**DEFAULT_VARS, **noise_settings_variables}

        noise_form_data = {}
        for key, description in NOISE_SETTINGS_DESCRIPTIONS.items():
            value = merged_noise_vars.get(key, None)
            is_default = True if DEFAULT_VARS.get(key) == value else False 
            noise_form_data[key] = {
                "value": value,
                "description": description,
                "is_default": is_default
            }
        # Get the selected knowledge base for this agent
        selected_knowledge = None
        if agent_knowledge_ids:
            # Get the first knowledge base ID associated with this agent
            knowledge_base_id = agent_knowledge_ids[0][1]
            selected_knowledge = session.execute(
                select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == knowledge_base_id)
            ).scalars().first()
        
        # Sync ElevenLabs knowledge base information if agent has ElevenLabs ID
        if agent.elvn_lab_agent_id and not agent.elvn_lab_knowledge_base:
            try:
                from elevenlabs_app.services.eleven_lab_agent_utils import ElevenLabsAgentCRUD
                elevenlabs_crud = ElevenLabsAgentCRUD()
                elevenlabs_agent = elevenlabs_crud.get_agent(agent.elvn_lab_agent_id)
                
                if "error" not in elevenlabs_agent:
                    # Extract knowledge base information from ElevenLabs
                    kb_files = []
                    if (elevenlabs_agent.get("conversation_config") and 
                        elevenlabs_agent["conversation_config"].get("agent") and 
                        elevenlabs_agent["conversation_config"]["agent"].get("prompt") and 
                        elevenlabs_agent["conversation_config"]["agent"]["prompt"].get("knowledge_base")):
                        
                        kb_files = elevenlabs_agent["conversation_config"]["agent"]["prompt"]["knowledge_base"]
                        print(f"🔍 Debug: Found {len(kb_files)} knowledge base files in ElevenLabs for agent {agent.elvn_lab_agent_id}")
                        
                        # Save knowledge base information to agent model
                        from datetime import datetime
                        kb_data = {
                            "files": kb_files,
                            "last_synced": str(datetime.now()),
                            "source": "elevenlabs"
                        }
                        AgentModel.update_elevenlabs_knowledge_base(agent_id_int, kb_data)
                        print(f"✅ Success: Saved ElevenLabs knowledge base info to agent {agent_id}")
                        
            except Exception as e:
                print(f"⚠️ Warning: Failed to sync ElevenLabs knowledge base: {str(e)}")
        
        custom_functions = ElevenLabsWebhookToolModel.get_all_by_agent(agent_id_int)
        daily_call_limit = DailyCallLimitModel.get_by_agent_id(agent_id_int)
        overall_token_limit = OverallTokenLimitModel.get_by_agent_id(agent_id_int)
        voices = VoiceModel.get_allowed_voices(user_id=user_id)
        llm_models = LLMModel.get_all()
        selected_elevenlab_model = agent.selected_model_obj.name
        elevenlab_model_rec = ElevenLabModel.get_by_name(selected_elevenlab_model)
        allowed_languages = elevenlab_model_rec.languages
        return templates.TemplateResponse(
            "ElevenLabs_Integration/web/update_agent.html",
            {
                "request": request,
                "voices": voices,
                "llm_models":llm_models,
                "allowed_languages":allowed_languages,
                "agent": agent,
                "knowledge_bases": knowledge_bases,
                "agent_knowledge_ids": agent_knowledge_ids,
                "selected_knowledge": selected_knowledge,
                "dynamic_variables": dynamic_variables,
                "noise_form_data":noise_form_data,
                "custom_functions": custom_functions,
                "host": os.getenv("HOST"),
                "daily_call_limit": daily_call_limit.set_value if daily_call_limit else 0,
                "overall_token_limit": overall_token_limit.overall_token_limit if overall_token_limit else 0,
                "per_call_token_limit": agent.per_call_token_limit if agent.per_call_token_limit else 0,
                "system_variables_data": system_variables_data
            },
        )
    except Exception as ex:
        return templates.TemplateResponse(
            "ElevenLabs_Integration/errors/display_error.html",
            {    
                "request": request,
                'error_message':'Some Error Occurred. Please ontact the support team.',
                'dev_error_message':str(ex)
            },
        )

@ElevenLabsWebRouter.get("/create_agent")
@check_session_expiry_redirect
async def create_agent(request: Request):
    
    user_id = request.session.get("user").get("user_id")
    knowledge_bases = KnowledgeBaseModel.get_all_by_user(user_id)
    voices = VoiceModel.get_allowed_voices(user_id=user_id)
    llm_models = LLMModel.get_all()
    selected_elevenlab_model = DEFAULT_MODEL_ELEVENLAB
    elevenlab_model_rec = ElevenLabModel.get_by_name(selected_elevenlab_model)
    allowed_languages = elevenlab_model_rec.languages
    return templates.TemplateResponse(
        "ElevenLabs_Integration/web/create_agent.html", 
        {
            "request": request,
            "voices": voices,
            "allowed_languages":allowed_languages,
            "llm_models":llm_models,
            "knowledge_bases":knowledge_bases,
            "host": os.getenv("HOST")
        }
    )

@ElevenLabsWebRouter.get("/preview_agent")
@check_session_expiry_redirect
async def elevenlabs_preview_agent(request: Request):
    """
    ElevenLabs Agent Preview - Uses ElevenLabs SDK for voice conversation
    """
    try:
        agent_id = request.query_params.get("agent_id")
        user_id = request.session.get("user", {}).get("user_id")
        scheme = request.url.scheme
        host = f"{scheme}://{request.headers.get('host')}"
        
        if not agent_id or not user_id:
            return templates.TemplateResponse(
                "ElevenLabs_Integration/web/error.html",
                {
                    "request": request,
                    "error_message": "Missing agent_id or user session",
                    "host": host
                }
            )
        
        # Get agent details using dynamic_id instead of id
        Session = sessionmaker(bind=engine)
        session = Session()
        agent_result = session.execute(select(AgentModel).where(AgentModel.dynamic_id == agent_id))
        agent = agent_result.scalars().first()
        
        if not agent:
            return templates.TemplateResponse(
                "ElevenLabs_Integration/web/error.html",
                {
                    "request": request,
                    "error_message": "Agent not found",
                    "host": host
                }
            )
        
        # Check if ElevenLabs API key is configured
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        if not elevenlabs_api_key:
            return templates.TemplateResponse(
                "ElevenLabs_Integration/web/error.html",
                {
                    "request": request,
                    "error_message": "ElevenLabs API key not configured. Please set ELEVENLABS_API_KEY environment variable.",
                    "host": host
                }
            )
        
        # Get agent's ElevenLabs ID or use a default one
        elevenlabs_agent_id = agent.elvn_lab_agent_id or "agent_0701k4ctqt62e6wa39y34y02p281"
        
        context = {
            "request": request,
            "agent_id": agent_id,  # This is the dynamic_id
            "agent_db_id": agent.id,  # This is the actual database ID
            "elevenlabs_agent_id": elevenlabs_agent_id,
            "agent_name": agent.agent_name,
            "welcome_msg": agent.welcome_msg or "Hello! How can I help you today?",
            "system_instruction": agent.agent_prompt or "You are a helpful AI assistant.",  # Use agent_prompt instead of system_instruction
            "host": host,
            "elevenlabs_api_key_configured": bool(elevenlabs_api_key)
        }
        
        return templates.TemplateResponse(
            "ElevenLabs_Integration/web/elevenlabs_preview.html",
            context
        )
        
    except Exception as e:
        return templates.TemplateResponse(
            "ElevenLabs_Integration/web/error.html",
            {
                "request": request,
                "error_message": f"Error loading preview: {str(e)}",
                "host": host
            }
        )


@ElevenLabsWebRouter.get("/call_history")
@check_session_expiry_redirect
async def call_history(request: Request, page: int = 1):
    from app.databases.models import AudioRecordings, VoiceModel
    agent_id = request.query_params.get("agent_id")
    audio_recordings = AudioRecordings.get_all_by_agent(agent_id)
    audio_recordings = sorted(audio_recordings, key=lambda x: x.created_at, reverse=True)
    items_per_page = 10
    start = (page - 1) * items_per_page
    end = start + items_per_page
    paginator = Paginator(audio_recordings, page, items_per_page, start, end)
    agent = AgentModel.get_by_id(agent_id)
    final_response = paginator.items

    # Get voice name instead of voice ID
    voice_name = "Unknown"
    if agent and agent.selected_voice:
        voice = VoiceModel.get_by_id(agent.selected_voice)
        if voice:
            voice_name = voice.voice_name

    return templates.TemplateResponse(
        "Web/call_history.html",
        {
            "request": request,
            "audio_recordings": final_response,  
            "page_obj": paginator,
            "agent_name": agent.agent_name,
            "selected_voice": voice_name,  # Now passing voice name instead of ID
            "agent_id": agent_id,
            "host": os.getenv("HOST")
        }
    )

