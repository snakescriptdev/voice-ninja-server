from fastapi import APIRouter,Request,UploadFile, Form, Depends
from fastapi.templating import Jinja2Templates
from elevenlabs_app.core import VoiceSettings
from elevenlabs_app.core.config import DEFAULT_VARS,NOISE_SETTINGS_DESCRIPTIONS
from elevenlabs_app.utils.helper import Paginator, check_session_expiry_redirect,get_logged_in_user
from fastapi.responses import RedirectResponse, FileResponse, Response, HTMLResponse,JSONResponse
from app.databases.models import AgentModel, KnowledgeBaseModel, agent_knowledge_association, UserModel, AgentConnectionModel, CustomFunctionModel, ApprovedDomainModel, DailyCallLimitModel, OverallTokenLimitModel,VoiceModel,LLMModel,ElevenLabModel
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
        
        # Check if agent has ElevenLabs knowledge base data but no local association
        if agent.elvn_lab_knowledge_base and not selected_knowledge:
            kb_files = agent.elvn_lab_knowledge_base.get("files", [])
            if kb_files:
                # Create a virtual knowledge base for display
                class VirtualKnowledgeBase:
                    def __init__(self, files):
                        self.id = "elevenlabs_virtual"
                        self.knowledge_base_name = f"ElevenLabs KB ({len(files)} files)"
                        self.files = files
                
                selected_knowledge = VirtualKnowledgeBase(kb_files)
                print(f"🔍 Debug: Using stored ElevenLabs knowledge base info: {selected_knowledge.knowledge_base_name}")
        
        # custom_functions = CustomFunctionModel.get_all_by_agent_id(agent_id_int)
      
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


# @ElevenLabsWebRouter.get("/chatbot-script.js/{agent_id}")
# def elevenlabs_chatbot_script_v1(request: Request, agent_id: str):
#     """
#     Dynamic JavaScript injection for ElevenLabs agents - Enhanced UI version for /v1/ router
#     """
#     try:
#         ws_protocol = "wss" if request.url.scheme == "https" else "ws"
#         agent = AgentModel.get_by_dynamic_id(agent_id)

#         if not agent:
#             response = Response("// Agent not found.", media_type="application/javascript")
#             response.headers['Cache-Control'] = 'public, max-age=3600'
#             return response

#         if not agent.elvn_lab_agent_id:
#             response = Response("// ElevenLabs agent ID not configured.", media_type="application/javascript")
#             response.headers['Cache-Control'] = 'public, max-age=3600'
#             return response

#         created_by = agent.created_by
#         domain = request.base_url.hostname
#         domains = os.getenv("DOMAIN_NAME", "").split(",")
#         host = os.getenv("HOST", str(request.base_url))
        
#         # Get agent appearance settings
#         appearances = AgentConnectionModel.get_by_agent_id(agent.id)
#         if not appearances:
#             # Set default appearance
#             appearances = type('obj', (object,), {
#                 'primary_color': '#0C7FDA',
#                 'secondary_color': '#99d2ff', 
#                 'pulse_color': '#ffffff',
#                 'icon_url': f'{host}/static/Web/images/default_voice_icon.png'
#             })
        
#         # Enhanced ElevenLabs widget with official ElevenLabs design
#         script_content = f'''
#         document.addEventListener('DOMContentLoaded', function() {{
#             (function() {{
#                 console.log("ElevenLabs Enhanced Design Mode Loading...");
                
#                 // Inject ElevenLabs WebSocket script
#                 const elevenLabsScript = document.createElement('script');
#                 elevenLabsScript.src = "{host}/static/js/elevenlabs_websocket.js";
#                 document.head.appendChild(elevenLabsScript);
                
#                 elevenLabsScript.onload = function() {{
#                     if (typeof ElevenLabsWebSocketClient === 'function') {{
#                         console.log("ElevenLabs client class available for agent: {agent_id}");
#                         // Don't auto-initialize, wait for user interaction
#                         window.elevenLabsAgentId = '{agent_id}';
#                         console.log("ElevenLabs agent ID set:", window.elevenLabsAgentId);
#                     }} else {{
#                         console.error("ElevenLabsWebSocketClient is not defined");
#                     }}
#                 }};

#                 // Create enhanced ElevenLabs widget with ElevenLabs official design
#                 const container = document.createElement('div');
#                 container.innerHTML = `
#                     <!-- ElevenLabs Agent Widget -->
#                     <div id="elevenlabs-widget" style="
#                         position: fixed;
#                         bottom: 20px;
#                         right: 20px;
#                         z-index: 10000;
#                         font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
#                     ">
#                         <!-- Language Selection Panel (initially hidden) -->
#                         <div id="language-panel" style="
#                             background: white;
#                             border-radius: 20px;
#                             padding: 20px;
#                             margin-bottom: 10px;
#                             box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
#                             display: none;
#                             min-width: 280px;
#                         ">
#                             <div style="margin-bottom: 15px;">
#                                 <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('en', '🇺🇸', 'ENGLISH')">
#                                     <span style="font-size: 20px; margin-right: 12px;">🇺🇸</span>
#                                     <span style="font-weight: 600; color: #1a1a1a;">ENGLISH</span>
#                                 </div>
#                                 <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('zh', '🇨🇳', 'CHINESE')">
#                                     <span style="font-size: 20px; margin-right: 12px;">🇨🇳</span>
#                                     <span style="font-weight: 600; color: #1a1a1a;">CHINESE</span>
#                                 </div>
#                                 <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('hr', '🇭🇷', 'CROATIAN')">
#                                     <span style="font-size: 20px; margin-right: 12px;">🇭🇷</span>
#                                     <span style="font-weight: 600; color: #1a1a1a;">CROATIAN</span>
#                                 </div>
#                                 <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('cs', '🇨🇿', 'CZECH')">
#                                     <span style="font-size: 20px; margin-right: 12px;">🇨🇿</span>
#                                     <span style="font-weight: 600; color: #1a1a1a;">CZECH</span>
#                                 </div>
#                                 <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('da', '🇩🇰', 'DANISH')">
#                                     <span style="font-size: 20px; margin-right: 12px;">🇩🇰</span>
#                                     <span style="font-weight: 600; color: #1a1a1a;">DANISH</span>
#                                 </div>
#                                 <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('nl', '🇳🇱', 'DUTCH')">
#                                     <span style="font-size: 20px; margin-right: 12px;">🇳🇱</span>
#                                     <span style="font-weight: 600; color: #1a1a1a;">DUTCH</span>
#                                 </div>
#                             </div>
#                         </div>
                        
#                         <!-- Main Control Panel -->
#                         <div id="main-panel" style="
#                             background: white;
#                             border-radius: 25px;
#                             padding: 20px;
#                             box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
#                             display: flex;
#                             align-items: center;
#                             gap: 15px;
#                             min-width: 280px;
#                         ">
#                             <!-- Voice Indicator -->
#                             <div id="voice-indicator" style="
#                                 width: 50px;
#                                 height: 50px;
#                                 border-radius: 50%;
#                                 background: linear-gradient(45deg, #00d4ff, #006eff);
#                                 display: flex;
#                                 align-items: center;
#                                 justify-content: center;
#                                 flex-shrink: 0;
#                                 transition: all 0.3s ease;
#                             ">
#                                 <div style="
#                                     width: 24px;
#                                     height: 24px;
#                                     border-radius: 50%;
#                                     background: radial-gradient(circle, rgba(255,255,255,0.8) 0%, rgba(255,255,255,0.4) 70%);
#                                 "></div>
#                             </div>
                            
#                             <!-- Action Button -->
#                             <button id="voice-chat-btn" onclick="toggleElevenLabsChat()" style="
#                                 background: #000;
#                                 color: white;
#                                 border: none;
#                                 border-radius: 25px;
#                                 padding: 12px 20px;
#                                 font-weight: 600;
#                                 font-size: 14px;
#                                 cursor: pointer;
#                                 transition: all 0.2s ease;
#                                 display: flex;
#                                 align-items: center;
#                                 gap: 8px;
#                                 flex: 1;
#                             ">
#                                 <i class="fas fa-phone" style="font-size: 14px;"></i>
#                                 <span id="btn-text">VOICE CHAT</span>
#                             </button>
                            
#                             <!-- Language Selector -->
#                             <button id="language-btn" onclick="toggleLanguagePanel()" style="
#                                 background: #f5f5f5;
#                                 border: 2px solid #e0e0e0;
#                                 border-radius: 20px;
#                                 padding: 8px 12px;
#                                 cursor: pointer;
#                                 display: flex;
#                                 align-items: center;
#                                 gap: 6px;
#                                 transition: all 0.2s ease;
#                             ">
#                                 <span id="selected-flag" style="font-size: 18px;">🇺🇸</span>
#                                 <i class="fas fa-chevron-down" style="font-size: 10px; color: #666;"></i>
#                             </button>
#                         </div>
                        
#                         <!-- Branding -->
#                         <div style="
#                             text-align: center;
#                             margin-top: 10px;
#                             font-size: 11px;
#                             color: #999;
#                             opacity: 0.7;
#                         ">
#                             Powered by Voice Ninja
#                         </div>
#                     </div>
#                 `;
#                 document.body.appendChild(container);

#                 // Add ElevenLabs-specific control functions
#                 window.isConnected = false;
#                 window.selectedLanguage = 'en';
                
#                 // Language selection function
#                 window.selectLanguage = function(code, flag, name) {{
#                     window.selectedLanguage = code;
#                     document.getElementById('selected-flag').innerText = flag;
#                     document.getElementById('language-panel').style.display = 'none';
#                     console.log('Language selected:', name, code);
#                 }};
                
#                 // Toggle language panel
#                 window.toggleLanguagePanel = function() {{
#                     const panel = document.getElementById('language-panel');
#                     panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
#                 }};
                
#                 // Main chat toggle function
#                 window.toggleElevenLabsChat = function() {{
#                     if (!window.isConnected) {{
#                         // Start connection
#                         document.getElementById('btn-text').innerText = 'CONNECTING...';
#                         document.getElementById('voice-chat-btn').style.background = '#666';
                        
#                         // Initialize ElevenLabs client when user first clicks
#                         if (!window.elevenLabsClient && window.elevenLabsAgentId) {{
#                             console.log("Creating ElevenLabs client for agent:", window.elevenLabsAgentId);
#                             window.elevenLabsClient = new ElevenLabsWebSocketClient(window.elevenLabsAgentId);
#                         }}
                        
#                         // Start ElevenLabs connection
#                         if (window.elevenLabsClient) {{
#                             console.log("Starting ElevenLabs connection...");
#                             window.elevenLabsClient.connect().then(() => {{
#                                 // Connection successful
#                                 window.isConnected = true;
#                                 document.getElementById('btn-text').innerHTML = '<i class="fas fa-times" style="font-size: 14px;"></i> END CALL';
#                                 document.getElementById('voice-chat-btn').style.background = '#000';
                                
#                                 // Animate voice indicator
#                                 const indicator = document.getElementById('voice-indicator');
#                                 indicator.style.animation = 'pulse 2s infinite';
#                                 indicator.style.background = 'linear-gradient(45deg, #00ff88, #00cc66)';
                                
#                                 console.log('✅ ElevenLabs connected successfully');
                                
#                                 // Listen for disconnection
#                                 window.elevenLabsClient.ws.onclose = function() {{
#                                     window.isConnected = false;
#                                     document.getElementById('btn-text').innerHTML = '<i class="fas fa-phone" style="font-size: 14px;"></i> VOICE CHAT';
#                                     document.getElementById('voice-chat-btn').style.background = '#000';
                                    
#                                     // Reset voice indicator
#                                     const indicator = document.getElementById('voice-indicator');
#                                     indicator.style.animation = 'none';
#                                     indicator.style.background = 'linear-gradient(45deg, #00d4ff, #006eff)';
                                    
#                                     console.log('❌ ElevenLabs disconnected');
#                                 }};
                                
#                             }}).catch(err => {{
#                                 console.error("Failed to connect to ElevenLabs:", err);
#                                 document.getElementById('btn-text').innerText = 'CONNECTION FAILED';
#                                 document.getElementById('voice-chat-btn').style.background = '#ff4444';
#                                 setTimeout(() => {{
#                                     document.getElementById('btn-text').innerHTML = '<i class="fas fa-phone" style="font-size: 14px;"></i> VOICE CHAT';
#                                     document.getElementById('voice-chat-btn').style.background = '#000';
#                                 }}, 3000);
#                             }});
#                         }}
#                     }} else {{
#                         // End connection
#                         if (window.elevenLabsClient) {{
#                             window.elevenLabsClient.disconnect();
#                             // UI will be updated by the onclose handler
#                         }}
#                     }}
#                 }};
                
#                 // Add CSS animations
#                 const style = document.createElement('style');
#                 style.textContent = `
#                     @keyframes pulse {{
#                         0% {{ box-shadow: 0 0 0 0 rgba(0, 255, 136, 0.7); }}
#                         70% {{ box-shadow: 0 0 0 10px rgba(0, 255, 136, 0); }}
#                         100% {{ box-shadow: 0 0 0 0 rgba(0, 255, 136, 0); }}
#                     }}
                    
#                     #elevenlabs-widget button:hover {{
#                         transform: translateY(-1px);
#                         box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
#                     }}
                    
#                     #language-panel div:hover {{
#                         background: #f8f9fa !important;
#                     }}
                    
#                     #voice-chat-btn:active {{
#                         transform: translateY(0);
#                     }}
#                 `;
#                 document.head.appendChild(style);
                
#                 // Close language panel when clicking outside
#                 document.addEventListener('click', function(event) {{
#                     const languagePanel = document.getElementById('language-panel');
#                     const languageBtn = document.getElementById('language-btn');
                    
#                     if (!languagePanel.contains(event.target) && !languageBtn.contains(event.target)) {{
#                         languagePanel.style.display = 'none';
#                     }}
#                 }});
#             }})();
#         }});
#         '''

#         headers = {
#             "Content-Type": "application/javascript",
#             "Cache-Control": "no-cache, no-store, must-revalidate",
#             "Pragma": "no-cache",
#             "Expires": "0"
#         }
#         return jsonify({"result":"success", "data": result}), 200
        
#     except Exception as e:
#         error_script = f'''
#         console.error("Error loading ElevenLabs agent script: {str(e)}");
#         document.addEventListener('DOMContentLoaded', function() {{
#             const errorDiv = document.createElement('div');
#             errorDiv.style.cssText = `
#                 position: fixed;
#                 bottom: 20px;
#                 right: 20px;
#                 background: #ff4444;
#                 color: white;
#                 padding: 15px;
#                 border-radius: 8px;
#                 z-index: 10000;
#             `;
#             errorDiv.textContent = 'Error loading agent: {str(e)}';
#             document.body.appendChild(errorDiv);
#         }});
#         '''
#         headers = {"Content-Type": "application/javascript", "Cache-Control": "no-cache"}
#         return Response(content=error_script, media_type="application/javascript", headers=headers)
