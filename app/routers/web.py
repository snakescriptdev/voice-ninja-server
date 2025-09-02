from fastapi import APIRouter,Request,UploadFile, Form, Depends
from fastapi.templating import Jinja2Templates
from app.core import VoiceSettings
from app.core.config import DEFAULT_VARS,NOISE_SETTINGS_DESCRIPTIONS
from app.utils.helper import Paginator, check_session_expiry_redirect,get_logged_in_user
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
router = APIRouter()

templates = Jinja2Templates(directory="templates")

def get_host_with_fallback():
    """Get host with proper fallback and URL scheme"""
    host = os.getenv("HOST", "localhost:8000")
    if not host.startswith(('http://', 'https://')):
        host = f"http://{host}"
    return host

@router.get("/signup")
async def index(request: Request):  
    user = request.session.get("user")
    if user and user.get("is_authenticated"):
        return RedirectResponse(url="/dashboard")
    
    # Get host with fallback
    host = get_host_with_fallback()
    
    return templates.TemplateResponse(
        "Web/signup.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "host": host
        }
    )


@router.get("/login")
async def login(request: Request):
    user = request.session.get("user")
    if user and user.get("is_authenticated"):
        return RedirectResponse(url="/dashboard")
    
    # Get host with fallback
    host = os.getenv("HOST", "http://localhost:8000")
    if not host.startswith(('http://', 'https://')):
        host = f"http://{host}"
    
    return templates.TemplateResponse(
        "Web/login.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "host": host
        }
    )

@router.get("/forget_password")
async def forget_password(request: Request):
    return templates.TemplateResponse(
        "Web/forget-password.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "host": os.getenv("HOST")
        }
    )


@router.get("/dashboard", name="dashboard")
@check_session_expiry_redirect
async def dashboard(request: Request, page: int = 1):
    from app.databases.models import AgentModel
    from app.databases.models import ApprovedDomainModel
    user = request.session.get("user")
    if not user or not user.get("is_authenticated"):
        return RedirectResponse(url="/login")
    domains = os.getenv("DOMAIN_NAME").split(",")
    for domain in domains:
        approved_domain = ApprovedDomainModel.check_domain_exists(domain, user.get("user_id"))
        if not approved_domain:
            ApprovedDomainModel.create(domain, user.get("user_id"))

    # Get all agents created by current user
    agents = AgentModel.get_all_by_user(user.get("user_id"))

    # Pagination
    items_per_page = 10
    start = (page - 1) * items_per_page
    end = start + items_per_page
    
    paginator = Paginator(agents, page, items_per_page, start, end)
    return templates.TemplateResponse(
        "Web/dashboard.html",
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "page_obj": paginator,
            "user": user,
            "host": os.getenv("HOST")
        }
    )



@router.get("/chatbot/")
async def index(request: Request):
    return templates.TemplateResponse(
        "connect.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "host": os.getenv("HOST")
        }
    )

@router.get("/chatbot/audio_list/")
async def audio_list(request: Request):
    return templates.TemplateResponse(
        "audio_list.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "enable_filters": False,
            "host": os.getenv("HOST")
        }
    )

@router.get("/create_agent")
@check_session_expiry_redirect
async def create_agent(request: Request):
    from app.databases.models import KnowledgeBaseModel
    user_id = request.session.get("user").get("user_id")
    knowledge_bases = KnowledgeBaseModel.get_all_by_user(user_id)
    voices = VoiceModel.get_allowed_voices(user_id=user_id)
    return templates.TemplateResponse(
        "Web/create_agent.html", 
        {
            "request": request,
            "voices": voices,
            "knowledge_bases":knowledge_bases,
            "host": os.getenv("HOST")
        }
    )


@router.get("/update_agent")
@check_session_expiry_redirect
async def update_agent(request: Request):
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
        "Web/update_agent.html",
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

@router.get("/knowledge-base", name="knowledge_base")
@check_session_expiry_redirect
async def knowledge_base(request: Request, page: int = 1):
    from app.databases.models import KnowledgeBaseModel, KnowledgeBaseFileModel
    knowledge_bases = KnowledgeBaseModel.get_all_by_user(request.session.get("user").get("user_id"))
    # Pagination
    items_per_page = 10
    start = (page - 1) * items_per_page
    end = start + items_per_page
    formatted_knowledge_bases = []
    for knowledge_base in knowledge_bases:
        files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
        print(f"🔍 Debug: Loading files for knowledge base {knowledge_base.id} ({knowledge_base.knowledge_base_name})")
        files_data = []
        for file in files:
            file_data = {
                "id": file.id,
                "name": file.file_name,
                "size": "",  # You can add file size if available   
                "url": f"/media/{file.file_path}",
                "knowledge_base_id": knowledge_base.id,
                "elevenlabs_doc_id": file.elevenlabs_doc_id,
                "elevenlabs_doc_name": file.elevenlabs_doc_name
            }
            print(f"🔍 Debug: File {file.file_name} - elevenlabs_doc_id: {file.elevenlabs_doc_id}")
            files_data.append(file_data)

        formatted_knowledge_bases.append({
            "id": knowledge_base.id,
            "knowledge_base_name": knowledge_base.knowledge_base_name,
            "files": files_data
        })
    
    paginator = Paginator(formatted_knowledge_bases, page, items_per_page, start, end)
    return templates.TemplateResponse(
        "Web/knowledge-base.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "page_obj": paginator,
            "host": os.getenv("HOST")
        }
    )

@router.get("/phone_number")
@check_session_expiry_redirect
async def phone_number(request: Request):
    # Safely extract user_id from session
    user = request.session.get("user", {})
    user_id = user.get("user_id")
    
    if not user_id:
        return {"error": "User not logged in."}  # Handle missing user session properly

    # Fetch agent and phone number data
    agents = AgentModel.get_all_by_user(user_id)  # Ensure this method exists


    return templates.TemplateResponse(
        "Web/phone-number.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "agents": agents,
            "host": os.getenv("HOST")
        }
    )
@router.get("/change_password")
@check_session_expiry_redirect
async def change_password(request: Request):

    return templates.TemplateResponse(
        "Web/change-password.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "host": os.getenv("HOST")
        }
    )


@router.get("/reset_password/{token}")  
async def reset_password(request: Request, token: str):
    from app.databases.models import ResetPasswordModel
    reset_password = ResetPasswordModel.get_by_token(token)
    if not reset_password:
        return RedirectResponse(url="/forget_password")
    
    return templates.TemplateResponse(
        "Web/reset-password.html", 
        {
            "request": request,
            "token": token  
        }
    )


@router.get("/verify-account/{token}")
async def verify_account(request: Request, token: str):
    from app.databases.models import ResetPasswordModel
    user = ResetPasswordModel.get_by_token(token)
    if not user:
        return RedirectResponse(url="/login")   
    return templates.TemplateResponse(
        "Web/verify_email_template.html", 
        {
            "request": request,
            "token": token,
            "host": os.getenv("HOST")
        }
    )

@router.get("/call_history")
@check_session_expiry_redirect
async def call_history(request: Request, page: int = 1):
    from app.databases.models import AudioRecordings
    agent_id = request.query_params.get("agent_id")
    audio_recordings = AudioRecordings.get_all_by_agent(agent_id)
    audio_recordings = sorted(audio_recordings, key=lambda x: x.created_at, reverse=True)
    items_per_page = 10
    start = (page - 1) * items_per_page
    end = start + items_per_page
    paginator = Paginator(audio_recordings, page, items_per_page, start, end)
    agent = AgentModel.get_by_id(agent_id)
    final_response = paginator.items

    return templates.TemplateResponse(
        "Web/call_history.html",
        {
            "request": request,
            "audio_recordings": final_response,  
            "page_obj": paginator,
            "agent_name": agent.agent_name,
            "selected_voice": agent.selected_voice,
            "agent_id": agent_id,
            "host": os.getenv("HOST")
        }
    )

@router.get("/web.js")
def serve_web_js():
    # Assuming web.js is in a 'static' directory
    js_file_path = os.path.join("static/js", "websocket.js")
    return FileResponse(js_file_path, media_type="application/javascript")


@router.get("/chatbot-script.js/{agent_id}")
def chatbot_script(request: Request, agent_id: str):
    ws_protocol = "wss" if request.url.scheme == "https" else "ws"
    agent = AgentModel.get_by_dynamic_id(agent_id)

    if not agent:
        response = HTMLResponse("Agent not found.", content_type="text/plain")
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response
    created_by = agent.created_by
    domain = request.base_url.hostname
    domains = os.getenv("DOMAIN_NAME").split(",")
    host = os.getenv("HOST")
    appearances = AgentConnectionModel.get_by_agent_id(agent.id)
    approved_domain = ApprovedDomainModel.check_domain_exists(domain, created_by)
    daily_call_limit = DailyCallLimitModel.get_by_agent_id(agent.id)
    overall_token_limit = OverallTokenLimitModel.get_by_agent_id(agent.id)
    if approved_domain or domain in domains:
        user = UserModel.get_by_id(created_by)
        if int(user.tokens) == 0:
            script_content = f'''document.addEventListener('DOMContentLoaded', function() {{
                    (function() {{
                        console.log("Script is running...");
                        
                        // Inject external scripts dynamically
                        const protobufScript = document.createElement('script');
                        protobufScript.src = "https://cdn.jsdelivr.net/npm/protobufjs@7.X.X/dist/protobuf.min.js";
                        document.head.appendChild(protobufScript);
                        
                        const webJsScript = document.createElement('script');
                        webJsScript.src = "{host}/static/js/websocket.js";
                        document.head.appendChild(webJsScript);
                        
                        const botStyle = document.createElement('link');
                        botStyle.rel = 'stylesheet';
                        botStyle.type = 'text/css';
                        botStyle.href = "{host}/static/Web/css/bot_style.css";
                        document.head.appendChild(botStyle);
                        
                        // Create and style popup dynamically
                        const popup = document.createElement('div');
                        popup.className = 'popup';
                        popup.style.cssText = `
                            background: linear-gradient(135deg, #0C7FDA, #99d2ff);
                            color: white;
                            padding: 20px;
                            border-radius: 12px;
                            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
                            text-align: center;
                            max-width: 350px;
                            position: fixed;
                            top: 50%;
                            left: 50%;
                            transform: translate(-50%, -50%);
                            display: none;
                        `;
                        
                        popup.innerHTML = `
                            <h2 style="font-size: 24px; margin-bottom: 10px;">Need More Tokens?</h2>
                            <p style="font-size: 18px; margin-bottom: 20px;">Get extra tokens now and keep enjoying premium features!</p>
                            <a href="{host}/payment" class='buy-button' style="
                                background: #fff;
                                color: #0C7FDA;
                                padding: 10px 20px;
                                font-size: 18px;
                                font-weight: bold;
                                border: none;
                                border-radius: 6px;
                                cursor: pointer;
                                text-decoration: none;
                                transition: background 0.3s;">
                                Buy Now
                            </a>
                        `;
                        
                        document.body.appendChild(popup);
                        setTimeout(() => {{ popup.style.display = 'block'; }}, 1000);
                    }})();
                }});

                        '''
            
        elif overall_token_limit and int(overall_token_limit.last_used_tokens) == int(overall_token_limit.overall_token_limit):
            script_content = f'''document.addEventListener('DOMContentLoaded', function() {{
                    (function() {{
                        console.log("Script is running...");
                        
                        // Inject external scripts dynamically
                        const protobufScript = document.createElement('script');
                        protobufScript.src = "https://cdn.jsdelivr.net/npm/protobufjs@7.X.X/dist/protobuf.min.js";
                        document.head.appendChild(protobufScript);
                        
                        const webJsScript = document.createElement('script');
                        webJsScript.src = "{host}/static/js/websocket.js";
                        document.head.appendChild(webJsScript);
                        
                        const botStyle = document.createElement('link');
                        botStyle.rel = 'stylesheet';
                        botStyle.type = 'text/css';
                        botStyle.href = "{host}/static/Web/css/bot_style.css";
                        document.head.appendChild(botStyle);
                        
                        // Create and style popup dynamically
                        const popup = document.createElement('div');
                        popup.className = 'popup';
                        popup.style.cssText = `
                            background: linear-gradient(135deg, #0C7FDA, #99d2ff);
                            color: white;
                            padding: 20px;
                            border-radius: 12px;
                            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
                            text-align: center;
                            max-width: 350px;
                            position: fixed;
                            top: 50%;
                            left: 50%;
                            transform: translate(-50%, -50%);
                            display: none;
                        `;
                        
                        popup.innerHTML = `
                            <h2 style="font-size: 24px; margin-bottom: 10px;">Overall Token Limit Reached</h2>
                            <p style="font-size: 18px; margin-bottom: 20px;">Upgrade now to unlock unlimited tokens and access all premium features!</p>
                            <a href="{host}/update_agent?agent_id={agent.id}" class='buy-button' style="
                                background: #fff;
                                color: #0C7FDA;
                                padding: 10px 20px;
                                font-size: 18px;
                                font-weight: bold;
                                border: none;
                                border-radius: 6px;
                                cursor: pointer;
                                text-decoration: none;
                                transition: background 0.3s;">
                                Update Token
                            </a>
                        `;
                        
                        document.body.appendChild(popup);
                        setTimeout(() => {{ popup.style.display = 'block'; }}, 1000);
                    }})();
                }});

                        '''
        
        elif daily_call_limit and int(daily_call_limit.set_value) == int(daily_call_limit.last_used):
            script_content = f'''document.addEventListener('DOMContentLoaded', function() {{
                    (function() {{
                        console.log("Script is running...");
                        
                        // Inject external scripts dynamically
                        const protobufScript = document.createElement('script');
                        protobufScript.src = "https://cdn.jsdelivr.net/npm/protobufjs@7.X.X/dist/protobuf.min.js";
                        document.head.appendChild(protobufScript);
                        
                        const webJsScript = document.createElement('script');
                        webJsScript.src = "{host}/static/js/websocket.js";
                        document.head.appendChild(webJsScript);
                        
                        const botStyle = document.createElement('link');
                        botStyle.rel = 'stylesheet';
                        botStyle.type = 'text/css';
                        botStyle.href = "{host}/static/Web/css/bot_style.css";
                        document.head.appendChild(botStyle);
                        
                        // Create and style popup dynamically
                        const popup = document.createElement('div');
                        popup.className = 'popup';
                        popup.style.cssText = `
                            background: linear-gradient(135deg, #0C7FDA, #99d2ff);
                            color: white;
                            padding: 20px;
                            border-radius: 12px;
                            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
                            text-align: center;
                            max-width: 350px;
                            position: fixed;
                            top: 50%;
                            left: 50%;
                            transform: translate(-50%, -50%);
                            display: none;
                        `;
                        
                        popup.innerHTML = `
                            <h2 style="font-size: 24px; margin-bottom: 10px;">Daily Call Limit Reached</h2>
                            <p style="font-size: 18px; margin-bottom: 20px;">You've reached your daily call limit. Please update your plan to continue using the service.</p>
                            <a href="{host}/update_agent?agent_id={agent.id}" class='buy-button' style="
                                background: #fff;
                                color: #0C7FDA;
                                padding: 10px 20px;
                                font-size: 18px;
                                font-weight: bold;
                                border: none;
                                border-radius: 6px;
                                cursor: pointer;
                                text-decoration: none;
                                transition: background 0.3s;">
                                Update Token
                            </a>
                        `;
                        
                        document.body.appendChild(popup);
                        setTimeout(() => {{ popup.style.display = 'block'; }}, 1000);
                    }})();
                }});

                        '''
        elif agent.is_design_enabled:
            script_content = f'''
                document.addEventListener('DOMContentLoaded', function() {{
                    (function() {{
                        // Inject HTML content
                        console.log("Script is running..."); // Debugging step
                        const protobufScript = document.createElement('script');
                        protobufScript.src = "https://cdn.jsdelivr.net/npm/protobufjs@7.X.X/dist/protobuf.min.js";
                        document.head.appendChild(protobufScript);

                        // Include the WebSocket script
                        const webJsScript = document.createElement('script');
                        webJsScript.src = "{host}/static/js/websocket.js";
                        document.head.appendChild(webJsScript);

                        
                        webJsScript.onload = function() {{
                            if (typeof WebSocketClient === 'function') {{
                                const client = new WebSocketClient({agent.id});
                            }} else {{
                                console.error("WebSocketClient is not defined");
                            }}
                        }};

                        const container = document.createElement('div');
                        container.innerHTML = `
                            <div class="voice_icon" onclick="toggleRecorder()" id="startCall" 
                                style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                <img src="{appearances.icon_url}" alt="voice_icon">
                            </div>
                            <div id="recorderControls" class="recorder-controls hidden" 
                                style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                <div class="settings">
                                    <div id="colorPalette" class="color-palette">
                                        <div class="color-option" 
                                            style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                        </div>
                                    </div>
                                </div>
                                <h1 id="status-text">Connect with me</h1>
                                <div class="status-indicator">
                                    <img src="{host}/static/Web/images/wave.gif" alt="voice_icon">
                                </div>
                                <button onclick="stopRecorder()" id="endCallPopup" 
                                        style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                    Stop Recording
                                </button>
                            </div>
                        `;
                        document.body.appendChild(container);
                    }})();
                }});
                '''
        else:
            script_content = f"""
            document.addEventListener('DOMContentLoaded', function() {{
                (function() {{
                    // Inject HTML content
                    // Add protobuf script
                    const protobufScript = document.createElement('script');
                    protobufScript.src = "https://cdn.jsdelivr.net/npm/protobufjs@7.X.X/dist/protobuf.min.js";
                    document.head.appendChild(protobufScript);

                    // Include the WebSocket script
                    const webJsScript = document.createElement('script');
                    webJsScript.src = "{host}/static/js/websocket.js";
                    document.head.appendChild(webJsScript);

                    webJsScript.onload = function() {{
                    if (typeof WebSocketClient === 'function') {{
                        const client = new WebSocketClient({agent.id});
                    }} else {{
                        console.error("WebSocketClient is not defined");
                    }}
                    }};
                    const style = document.createElement('style');
                    style.textContent = `
                        .phone_numder_outer {{
                            position: fixed;
                            bottom: 20px;
                            right: 20px;
                            z-index: 1000;
                        }}
                        .phone_numder_msg {{
                            background-image: url(https://snakescript.com/images_ai_voice_agent/cloud-msg-box.svg);
                            position: absolute;
                            bottom: 63px;
                            right: 30px;
                            background-repeat: no-repeat;
                            background-size: cover;
                            width: 250px;
                            height: 180px;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                        }}
                        .close_msg {{
                            position: absolute;
                            top: 24px;
                            z-index: 9999;
                            right: 19px;
                            height: 30px;
                            width: 30px;
                        }}
                        .phone_numder_msg h2 {{
                            font-weight: 500;
                            font-size: 14px;
                            line-height: 26px;
                            color: #ffffff;
                            margin-bottom: 0;
                        }}
                        .phone_numder_msg h2 span {{
                            display: block;
                            font-size: 22px;
                            margin-bottom: 0;
                        }}
                        .whatsapp_outer_mobile {{
                            display: block;
                        }}
                        .micro {{
                            position: relative;
                        }}
                        .micro:before,
                        .micro:after {{
                            position: absolute;
                            content: "";
                            top: -42px;
                            right: 0;
                            bottom: 0;
                            left: 0;
                            border: solid 3px #f00;
                            border-radius: 50%;
                            height: 50px;
                            width: 50px;
                            z-index: -1;
                        }}
                        .micro:before {{
                            animation: ripple 2s linear infinite;
                        }}
                        .micro:after {{
                            animation: ripple 2s 1s linear infinite;
                        }}
                        @keyframes ripple {{
                            to {{
                                transform: scale(2);
                                opacity: 0;
                            }}
                        }}
                        .whatsapp_outer_mobile img {{
                            width: 36px;
                            height: 36px;
                            background: #e50707;
                            border-radius: 100%;
                            padding: 10px 10px;
                        }}
                        .call-btn {{
                            display: none;
                            text-align: center;
                            margin-top: 20px;
                        }}
                    `;
                    document.head.appendChild(style);
                    const container = document.createElement('div');
                container.className = 'phone_numder_outer';
                container.innerHTML = `
                    <div class="phone_numder_msg" id="messageBox">
                        <div class="close_msg">
                            <img src="https://snakescript.com/svg_ai_voice_agent/close_msg.svg" class="img-fluid" style="cursor: pointer;" onclick="document.getElementById('messageBox').style.display='none'">
                        </div>
                        <h2>  <span> Hello 👋</span>
                        I am Sage, your AI agent.
                        <span>Let's Talk!</span>
                        </h2>
                    </div>
                    <div class="whatsapp_outer_mobile">
                        <span class="micro" id="startCall">
                        <img src="https://snakescript.com/images_ai_voice_agent/microphone.svg" class="img-fluid" style="cursor: pointer;">
                        </span>
                    </div>
                `;
                document.body.appendChild(container);

                // Add click handler for close button
                document.querySelector('.close_msg img').addEventListener('click', function() {{
                    document.querySelector('.phone_numder_msg').style.display = 'none';
                }});

                // Add call popup HTML
                const callPopup = document.createElement('div');
                callPopup.id = 'callPopup';
                callPopup.className = 'call-popup';
                callPopup.innerHTML = `
                    <div class="popup-content">
                        <div class="popup-header">
                            <div class="app-title">
                                <img src="https://snakescript.com/images_ai_voice_agent/user.png" alt="AI Brain" style="height:38px" />
                                SAGE
                            </div>
                            <button type="button" id="closePopup" class="close-btn">
                                <svg fill="#ffffff" height="15px" width="15px" version="1.1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 490 490">
                                    <polygon points="456.851,0 245,212.564 33.149,0 0.708,32.337 212.669,245.004 0.708,457.678 33.149,490 245,277.443 456.851,490 489.292,457.678 277.331,245.004 489.292,32.337"/>
                                </svg>
                            </button>
                        </div>
                        <div class="popup-body">
                        <div class="brain-container text-center">
                            <h3 id="status-text">Say something..</h3>
                        </div>
                            <div class="whatsapp_outer_mobile">
                        <span class="micro" id="startCall">
                        <img src="https://snakescript.com/images_ai_voice_agent/microphone.svg" class="img-fluid" style="cursor: pointer;">
                        </span>
                    </div>
                    </div>
                        <div id="conversationLog" class="conversation-log" style="display:none;"></div>
                        <div class="text-center mb-4">
                            <button id="endCallPopup" class="end-call-btn">
                                <svg fill="#ffffff" height="11px" width="11px" version="1.1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 490 490">
                                    <polygon points="456.851,0 245,212.564 33.149,0 0.708,32.337 212.669,245.004 0.708,457.678 33.149,490 245,277.443 456.851,490 489.292,457.678 277.331,245.004 489.292,32.337"/>
                                </svg> 
                                End Call
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(callPopup);

                // Add event listeners
                document.getElementById('startCall').addEventListener('click', function() {{
                    document.getElementById('callPopup').style.display = 'block';
                }});

                document.getElementById('closePopup').addEventListener('click', function() {{
                    document.getElementById('callPopup').style.display = 'none';
                    document.getElementById('endCallPopup').click();
                }});

                document.getElementById('endCallPopup').addEventListener('click', function() {{
                    document.getElementById('callPopup').style.display = 'none';
                }});

                // Add the same CSS styles as in chatbot-new.js
                const new_style = document.createElement('style');
                new_style.textContent = `
                    @import url('https://fonts.googleapis.com/css2?family=Roboto:ital,wght@0,100..900;1,100..900&display=swap');
                    
                    body {{
                            font-family: 'Roboto', sans-serif;
                            margin:0px;
                            padding:0px;
                        }}

                        h3 {{
                        font-size: 24px;
                        }}
                        .call-popup {{
                            display: none;
                            position: fixed;
                        
                            width: 100%;
                            height: 100%;
                            background: rgba(0, 0, 0, 0.85);
                            z-index: 9999;
                    }}

                    .popup-body {{
                        display: flex;
                        justify-content: space-between;
                        flex-direction: column;
                        align-items: center;
                        padding-bottom: 100px
                    }}
                        
                        .popup-content {{
                            position: fixed;
                            top: 50%;
                            left: 50%;
                            transform: translate(-50%, -50%);
                            background: #000000;
                            border-radius: 12px;
                            padding-bottom: 25px;
                            width: 90%;
                            max-width: 600px;
                            height: auto;
                            max-height: 700px;
                            display: flex;
                            flex-direction: column;
                            color: white;
                            border: 1px solid #373737;
                        }}
                        
                        .popup-header {{
                            display: flex;
                            justify-content: space-between;
                            align-items: center;
                            padding: 16px 20px;
                            background: rgba(255, 255, 255, 0);
                            z-index: 9;
                            border-bottom: 1px solid #373737;
                    }}
                        
                        .app-title {{
                            display: flex;
                            align-items: center;
                            gap: 8px;
                            font-size: 16px;
                            font-weight: 500;
                        }}
                        
                        .globe-icon {{
                            font-size: 20px;
                    }}
                        
                        .close-btn {{
                            background: #ff0000c7;
                            border: none;
                            color: #fff;
                            font-size: 24px;
                            cursor: pointer;
                            padding: 0;
                            line-height: 1;
                            border-radius: 100%;
                            height: 40px;
                            width: 40px;
                            position: relative;
                    }}
                    .close-btn svg {{
                            width: 15px;
                            position: absolute;
                            top: 12px;
                            height: 15px;
                            right: 12px;
                        }}
                        
                        .close-btn:hover {{
                            color: white;
                        }}
                        
                        .call-status {{
                            text-align: center;
                        padding: 100px 20px 100px 20px;
                        }}
                        
                        .brain-container {{
                            margin-top: 36px;
                        }}
                        
                        .brain-image {{
                            height: 140px;
                            margin: 0 auto;
                        }}
                        
                        .status-text {{
                            color: rgba(255, 255, 255, 0.8);
                            margin: 0;
                            font-size: 16px;
                        }}
                        
                        .conversation-log {{
                            flex-grow: 1;
                            overflow-y: auto;
                            padding: 20px;
                            display: flex;
                            flex-direction: column;
                            gap: 15px;
                            background: rgba(255, 255, 255, 0);
                            padding-top: 20px;
                        }}
                        
                        .message {{
                            padding: 12px 16px;
                            border-radius: 8px;
                            max-width: 80%;
                            line-height: 1.4;
                        }}
                        
                        .user-message {{
                            background: rgba(255, 255, 255, 0.1);
                            margin-left: auto;
                            color: white;
                        }}
                        
                        .agent-message {{
                            background: #1a1a1a;
                            margin-right: auto;
                            color: rgba(255, 255, 255, 0.9);
                        }}
                        
                        .system-message {{
                            background: rgba(128, 128, 128, 0.2);
                            margin: 0 auto;
                            color: rgba(255, 255, 255, 0.7);
                            font-style: italic;
                            font-size: 0.9em;
                        }}
                        
                        .button-container {{
                            padding: 20px;
                            text-align: center;
                            background: rgba(0, 0, 0, 0.3);
                        }}
                        
                        .end-call-btn {{
                        background: #831410 !important;
                        color: white;
                        border: none;
                        padding: 12px 24px;
                        border-radius: 25px;
                        cursor: pointer;
                        font-size: 14px;
                        font-weight: 500;
                        display: flex;
                        align-items: center;
                        gap: 10px;
                        margin: 0 auto;
                        text-align: center;
                        justify-content: center;
                        transition: all .4s ease-in-out;
                        transition: all 0.5s ease-in-out;
                        box-shadow: 0 0 10px 0 #f71b26 inset, 0 0 20px 2px #f71b26;
                        border: 1px solid #ffffff;
                        }}
                        
                        .end-call-btn:hover {{
                            background: #c82333;
                        }}

                        ::-webkit-scrollbar {{
                            width: 8px;
                        }}

                        ::-webkit-scrollbar-track {{
                            background: rgba(255, 255, 255, 0.05);
                        }}

                        ::-webkit-scrollbar-thumb {{
                            background: rgba(255, 255, 255, 0.2);
                            border-radius: 4px;
                        }}

                        ::-webkit-scrollbar-thumb:hover {{
                            background: rgba(255, 255, 255, 0.3);
                        }}
                `;
                document.head.appendChild(style);
                document.head.appendChild(new_style);
                }})();
            }});
            """
        
        headers = {
            'Cache-Control': 'no-cache, must-revalidate',
            'Content-Type': 'application/javascript'
        }
        return Response(content=script_content, media_type="application/javascript", headers=headers)
    else:
        return RedirectResponse(url="/error")


@router.get("/preview_agent")
async def preview_agent(request: Request):
    agent_id = request.query_params.get("agent_id")
    user_id = request.session.get("user").get("user_id")
    scheme = request.url.scheme
    host = f"{scheme}://{request.headers.get('host')}" 
    context = {"request": request, "agent_id": agent_id, "host": host}
    domain = request.base_url.hostname
    domains = os.getenv("DOMAIN_NAME").split(",")


    approved_domain = ApprovedDomainModel.check_domain_exists(domain, user_id)
    if approved_domain or domain in domains:
        return templates.TemplateResponse("testing.html", context)
    else:
        return RedirectResponse(url="/error")




@router.get("/payment")
@check_session_expiry_redirect
async def payment(request: Request):
    return templates.TemplateResponse(
        "Web/razorpay_payment.html", 
        {
            "request": request,
            "host": os.getenv("HOST")
        }
    )


@router.get("/payment_success")
@check_session_expiry_redirect
async def payment_success(request: Request):
    from app.databases.models import PaymentModel
    payment = PaymentModel.get_by_order_id(request.query_params.get("order_id"))
    if not payment:
        return RedirectResponse(url="/payment_failed?message=order_not_found")
    return templates.TemplateResponse(
        "Web/payment_success.html", 
        {
            "request": request,
            "coins": request.query_params.get("coins"),
            "amount": request.query_params.get("amount"),
            "order_id": request.query_params.get("order_id"),
            "host": os.getenv("HOST")
        }
    )


@router.get("/payment_failed")
@check_session_expiry_redirect
async def payment_failed(request: Request):
    return templates.TemplateResponse(
        "Web/payment_failed.html", 
        {
            "request": request,
            "message": request.query_params.get("message"),
            "host": os.getenv("HOST")
        }
    )


@router.get("/get_total_tokens")
async def get_total_tokens(request: Request):
    user_id = request.query_params.get("user_id")
    user = UserModel.get_by_id(user_id)
    return {"total_tokens": user.tokens}



@router.get("/webhook")
@check_session_expiry_redirect
async def webhook(request: Request):
    user_id = request.session.get("user").get("user_id")
    from app.databases.models import WebhookModel
    webhooks = WebhookModel.get_all_by_user(user_id)
    return templates.TemplateResponse(
        "Web/webhook.html", 
        {
            "request": request, 
            "webhooks": webhooks,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "host": os.getenv("HOST")
        }
    )



@router.get("/approved_domains")
@check_session_expiry_redirect
async def approved_domains(request: Request):
    user_id = request.session.get("user").get("user_id")
    approved_domains = ApprovedDomainModel.get_all_by_user(user_id)
    domains = os.getenv("DOMAIN_NAME").split(",")
    for domain in domains:
        approved_domain = ApprovedDomainModel.check_domain_exists(domain, user_id)
        if not approved_domain:
            ApprovedDomainModel.create(domain, user_id)
    return templates.TemplateResponse(
        "Web/approved_domains.html", 
        {
            "request": request, 
            "approved_domains": approved_domains,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "configured_domains": domains,
            "host": os.getenv("HOST")
        }
    )


@router.get("/error")
async def error(request: Request):
    return templates.TemplateResponse(
        "Web/error.html", 
        {
            "request": request,
            "host": os.getenv("HOST")
        }
    )


@router.get("/")
async def home(request: Request):
    user = request.session.get("user")
    if user and user.get("is_authenticated"):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        "Web/home.html",
        {"request": request, "host": os.getenv("HOST")}
    )

@router.get("/custom-voice-dashboard", name="custom_voice_dashboard")
@check_session_expiry_redirect
async def dashboard(request: Request, page: int = 1, search: str = None):
    user = request.session.get("user")
    if not user or not user.get("is_authenticated"):
        return RedirectResponse(url="/login")

    voices = VoiceModel.get_all_by_user(user.get("user_id"))

    if search:
        voices = [v for v in voices if search.lower() in v.voice_name.lower()]

    items_per_page = 10
    start = (page - 1) * items_per_page
    end = start + items_per_page
    paginator = Paginator(voices, page, items_per_page, start, end)

    return templates.TemplateResponse(
        "Web/custom_voice_dashboard.html",
        {
            "request": request,
            "page_obj": paginator,
            "user": user,
            "host": os.getenv("HOST")
        }
    )

@router.post("/api/create_voice")
async def create_voice(request: Request, voice_name: str = Form(...), audio_file: UploadFile | None = None):
    user = get_logged_in_user(request)
    if not user:
        return JSONResponse({"status": False, "message": "Login required"}, status_code=401)

    try:
        payload, error = validate_create_voice_request(voice_name, audio_file)
        if error:
            return error

        # check if same voice exists for this user
        exists = VoiceModel.get_by_name_and_user(payload.voice_name, user["user_id"])
        if exists:
            return JSONResponse({"status": False, "message": f"Voice name '{payload.voice_name}' already exists."})

        file_path = None
        save_dir = "static/uploads/custom_voices"
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, audio_file.filename)
        with open(save_path, "wb") as f:
            shutil.copyfileobj(audio_file.file, f)
        file_path = save_path

        elevenlabs_obj = ElevenLabsUtils()
        response = elevenlabs_obj.create_cloned_voice(file_path=file_path, name=payload.voice_name)
        if response.status:
            elevenlabs_voice_id = response.data.get("voice_id")
        else:
            return JSONResponse({"status": False,"error":f"{str(response.error_message)}","message": "Error in creating voice at elevenlabs."})

        voice = VoiceModel.create(
            voice_name=payload.voice_name,
            user_id=user["user_id"],
            audio_file=file_path,
            is_custom_voice=True,
            elevenlabs_voice_id = elevenlabs_voice_id
        )
        return JSONResponse({"status": True, "message": f"Voice '{payload.voice_name}' created successfully."})
    except SQLAlchemyError:
        return JSONResponse({"status": False, "message": "Database error occurred."})
    except Exception as e:
        return JSONResponse({"status": False,"message":"Some Error Occurred","error": str(e)})


@router.post("/api/edit_voice")
async def edit_voice(request: Request, payload: dict):
    user = get_logged_in_user(request)
    if not user:
        return JSONResponse(
            {"status": False, "message": "Login required"},
            status_code=401,
        )

    validation_error = validate_edit_voice(payload)
    if validation_error:
        return validation_error

    try:
        voice = VoiceModel.get_by_id(payload["voice_id"])
        if not voice:
            return JSONResponse({"status": False, "message": "Voice not found."}, status_code=404)

        if voice.user_id != user["user_id"]:
            return JSONResponse({"status": False, "message": "Unauthorized."}, status_code=403)

        if payload["voice_name"] == voice.voice_name:
            return JSONResponse({"status": False, "message": "No voice name change detected."}, status_code=400)

        exists = VoiceModel.get_by_name_and_user(payload["voice_name"], user["user_id"])
        if exists and exists.id != voice.id:
            return JSONResponse({"status": False, "message": "Voice name already exists."}, status_code=400)

        elevenlabs_obj = ElevenLabsUtils()
        voice_id = voice.elevenlabs_voice_id
        if not voice_id:
            return JSONResponse({"status": True, "message": f"Voice not found on elevenlabs. Please create new one."})

        new_voice_response = elevenlabs_obj.edit_voice_name(voice_id=voice.elevenlabs_voice_id, new_name=payload.get("voice_name"))
        if not new_voice_response.status:
            return JSONResponse({"status": False, "message": f"Error in updating voice on elevenlabs '{new_voice_response.error_message}'."})
        
        updated_voice = VoiceModel.update(payload["voice_id"], voice_name=payload["voice_name"])
        return JSONResponse({"status": True, "message": f"Voice name updated to '{payload['voice_name']}'."})

    except Exception as e:
        return JSONResponse(
            {"status": False, "message": "Some Error Occurred", "error": str(e)},
            status_code=500,
        )


@router.delete("/api/delete_voice")
async def delete_voice(request:Request, voice_id: int = Query(...)):
    user = get_logged_in_user(request)
    if not user:
        return JSONResponse(
            {"status": False, "message": "Login required"},
            status_code=401,
        )

    validation_error = validate_delete_voice(voice_id)
    if validation_error:
        return validation_error

    try:
        voice = VoiceModel.get_by_id(voice_id)
        if not voice:
            return JSONResponse({"status": False, "message": "Voice not found."}, status_code=404)

        if voice.user_id != user["user_id"]:
            return JSONResponse({"status": False, "message": "Unauthorized."}, status_code=403)

        if voice.audio_file and os.path.exists(voice.audio_file):
            os.remove(voice.audio_file)

        voice_id_to_delete = voice.elevenlabs_voice_id
        if voice_id_to_delete:
            elevenlabs_obj = ElevenLabsUtils()
            response = elevenlabs_obj.delete_voice(voice_id=voice_id_to_delete)
            if not response.status:
                return JSONResponse({"status": False,"error":f"{str(response.error_message)}","message": "Error in deleting voice at elevenlabs."})

        VoiceModel.delete(voice_id)
        return JSONResponse({"status": True, "message": f"Voice '{voice.voice_name}' deleted successfully."})

    except Exception as e:
        return JSONResponse(
            {"status": False, "message": "Some Error Occurred", "error": str(e)},
            status_code=500,
        )
