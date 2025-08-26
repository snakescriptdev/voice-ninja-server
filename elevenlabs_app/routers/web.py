from fastapi import APIRouter,Request,UploadFile, Form, Depends
from fastapi.templating import Jinja2Templates
from elevenlabs_app.core import VoiceSettings
from elevenlabs_app.core.config import DEFAULT_VARS,NOISE_SETTINGS_DESCRIPTIONS
from elevenlabs_app.utils.helper import Paginator, check_session_expiry_redirect,get_logged_in_user
from fastapi.responses import RedirectResponse, FileResponse, Response, HTMLResponse,JSONResponse
from app.databases.models import AgentModel, KnowledgeBaseModel, agent_knowledge_association, UserModel, AgentConnectionModel, CustomFunctionModel, ApprovedDomainModel, DailyCallLimitModel, OverallTokenLimitModel,VoiceModel
from sqlalchemy.orm import sessionmaker
from app.databases.models import engine
import os, shutil
from dotenv import load_dotenv
from fastapi import Query

from sqlalchemy.exc import SQLAlchemyError
from app.routers.schemas.voice_schemas import (
    validate_create_voice_request,
    validate_edit_voice,
    validate_delete_voice,
)
from app.services.elevenlabs_utils import ElevenLabsUtils
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
        
        from sqlalchemy import select, insert, delete
        Session = sessionmaker(bind=engine)
        session = Session()
        # Fetch agent knowledge associations
        result = session.execute(
            select(agent_knowledge_association).where(agent_knowledge_association.c.agent_id == agent_id)
        )
        agent_knowledge_ids = [(row.agent_id, row.knowledge_base_id) for row in result.fetchall()]

        # Fetch agent details
        agent_result =  session.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent = agent_result.scalars().first()

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
        
        custom_functions = CustomFunctionModel.get_all_by_agent_id(agent_id)
        daily_call_limit = DailyCallLimitModel.get_by_agent_id(agent_id)
        overall_token_limit = OverallTokenLimitModel.get_by_agent_id(agent_id)
        voices = VoiceModel.get_allowed_voices(user_id=user_id)
        return templates.TemplateResponse(
            "ElevenLabs_Integration/web/update_agent.html",
            {
                "request": request,
                "voices": voices,
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
                "per_call_token_limit": agent.per_call_token_limit if agent.per_call_token_limit else 0
            },
        )
    except Exception as ex:
        return templates.TemplateResponse(
            "web/display_error.html",
            {
                'error_message':'Some Error Occurred. Please ontact the support team.',
                'dev_error_message':str(ex)
            },
        )

@ElevenLabsWebRouter.get("/create_agent")
@check_session_expiry_redirect
async def create_agent(request: Request):
    from app.databases.models import KnowledgeBaseModel
    user_id = request.session.get("user").get("user_id")
    knowledge_bases = KnowledgeBaseModel.get_all_by_user(user_id)
    voices = VoiceModel.get_allowed_voices(user_id=user_id)
    return templates.TemplateResponse(
        "ElevenLabs_Integration/web/create_agent.html", 
        {
            "request": request,
            "voices": voices,
            "knowledge_bases":knowledge_bases,
            "host": os.getenv("HOST")
        }
    )
