from fastapi import APIRouter,Request
from fastapi.templating import Jinja2Templates
from app.core import VoiceSettings
from app.utils.helper import Paginator, check_session_expiry_redirect
from fastapi.responses import RedirectResponse, FileResponse, Response, HTMLResponse
from app.databases.models import AgentModel, KnowledgeBaseModel, agent_knowledge_association, UserModel, AgentConnectionModel
from sqlalchemy.orm import sessionmaker
from app.databases.models import engine
import os

router = APIRouter()

templates = Jinja2Templates(directory="templates")

@router.get("/")
async def index(request: Request):  
    user = request.session.get("user")
    if user and user.get("is_authenticated"):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        "Web/signup.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES
        }
    )


@router.get("/login")
async def login(request: Request):
    user = request.session.get("user")
    if user and user.get("is_authenticated"):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        "Web/login.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES
        }
    )

@router.get("/forget_password")
async def forget_password(request: Request):
    return templates.TemplateResponse(
        "Web/forget-password.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES
        }
    )


@router.get("/dashboard", name="dashboard")
@check_session_expiry_redirect
async def dashboard(request: Request, page: int = 1):
    from app.databases.models import AgentModel

    user = request.session.get("user")

    if not user or not user.get("is_authenticated"):
        return RedirectResponse(url="/login")

    # Get all agents created by current user
    agents = AgentModel.get_all_by_user(user.get("user_id"))

    # Pagination
    items_per_page = 10
    start = (page - 1) * items_per_page
    end = start + items_per_page
    
    paginator = Paginator(agents, page, items_per_page, start, end)
    return templates.TemplateResponse(
        "Web/home.html",
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "page_obj": paginator,
            "user": user
        }
    )



@router.get("/chatbot/")
async def index(request: Request):
    return templates.TemplateResponse(
        "connect.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES
        }
    )

@router.get("/chatbot/audio_list/")
async def audio_list(request: Request):
    return templates.TemplateResponse(
        "audio_list.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "enable_filters": False
        }
    )

@router.get("/create_agent")
@check_session_expiry_redirect
async def create_agent(request: Request):
    from app.databases.models import KnowledgeBaseModel
    user_id = request.session.get("user").get("user_id")
    knowledge_bases = KnowledgeBaseModel.get_all_by_user(user_id)
    return templates.TemplateResponse(
        "Web/create_agent.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "knowledge_bases":knowledge_bases
        }
    )


@router.get("/update-agent")
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
    # Get the selected knowledge base for this agent
    selected_knowledge = None
    if agent_knowledge_ids:
        # Get the first knowledge base ID associated with this agent
        knowledge_base_id = agent_knowledge_ids[0][1]
        selected_knowledge = session.execute(
            select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == knowledge_base_id)
        ).scalars().first()
    return templates.TemplateResponse(
        "Web/create_agent.html",
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES,
            "agent": agent,
            "knowledge_bases": knowledge_bases,
            "agent_knowledge_ids": agent_knowledge_ids,
            "selected_knowledge": selected_knowledge
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
        files_data = []
        for file in files:
            files_data.append(
                {
                    "name": file.file_name,
                    "size": "",  # You can add file size if available   
                    "url": f"/media/{file.file_path}"
                }   
            )

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
            "page_obj": paginator
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
        }
    )
@router.get("/change_password")
@check_session_expiry_redirect
async def change_password(request: Request):

    return templates.TemplateResponse(
        "Web/change-password.html", 
        {
            "request": request,
            "voices": VoiceSettings.ALLOWED_VOICES
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
            "token": token
        }
    )

@router.get("/call_history")
@check_session_expiry_redirect
async def call_history(request: Request, page: int = 1):
    from app.databases.models import AudioRecordings
    agent_id = request.query_params.get("agent_id")
    audio_recordings = AudioRecordings.get_all_by_agent(agent_id)
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
            "agent_id": agent_id
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
    agent = AgentModel.get_by_id(int(agent_id))

    if not agent:
        response = HTMLResponse("Agent not found.", content_type="text/plain")
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response
    
    appearances = AgentConnectionModel.get_by_agent_id(agent_id)
    
    script_content = f'''
    document.addEventListener('DOMContentLoaded', function() {{
        (function() {{
            // Inject HTML content
            // Add protobuf script
            const protobufScript = document.createElement('script');
            protobufScript.src = "https://cdn.jsdelivr.net/npm/protobufjs@7.X.X/dist/protobuf.min.js";
            document.head.appendChild(protobufScript);

            // Include the WebSocket script
            const webJsScript = document.createElement('script');
            webJsScript.src = "/static/js/websocket.js";
            document.head.appendChild(webJsScript);

            webJsScript.onload = function() {{
            if (typeof WebSocketClient === 'function') {{
                const client = new WebSocketClient({agent_id});
            }} else {{
                console.error("WebSocketClient is not defined");
            }}
            }};

            const container = document.createElement('div');
            container.innerHTML = `
                <link rel="stylesheet" type="text/css" href="/static/Web/css/bot_style.css">
                <div class="voice_icon" onclick="toggleRecorder()" id="start-btn" style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                    <img src="{appearances.icon_url}" alt="voice_icon">
                </div>
                <div id="recorderControls" class="recorder-controls hidden" style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                    <div class="settings">
                        <div id="colorPalette" class="color-palette">
                            <div class="color-option" style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});"></div>
                        </div>
                    </div>
                    <h1>Connect with me</h1>
                    <div class="status-indicator">
                        <img src="static/Web/images/wave.gif" alt="voice_icon">
                    </div>
                    <button onclick="stopRecorder()" id="stop-btn" style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">Stop Recording</button>
                </div>
            `;
            document.body.appendChild(container);
        }})();
    }});
    '''
    
    headers = {
        'Cache-Control': 'public, max-age=3600',
        'Content-Type': 'application/javascript'
    }
    
    return Response(content=script_content, media_type="application/javascript", headers=headers)

@router.get("/testing")
async def testing(request: Request):
    # Provide a context dictionary, even if it's empty
    context = {"request": request}
    return templates.TemplateResponse("testing.html", context)