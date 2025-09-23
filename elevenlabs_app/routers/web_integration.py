from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.databases.models import (
    AgentModel, 
    UserModel, 
    ApprovedDomainModel,
    DailyCallLimitModel,
    OverallTokenLimitModel,
    AgentConnectionModel,
    AudioRecordings,
    CallModel,
    ConversationModel
)
from sqlalchemy.orm import joinedload
from sqlalchemy import desc, and_
from fastapi_sqlalchemy import db
import os
import math
from loguru import logger

ElevenLabsWebRouter = APIRouter(tags=["elevenlabs-web"])

# Setup templates
templates = Jinja2Templates(directory="templates")


# Helper function for authentication (simplified for ElevenLabs)
def get_current_user(request: Request):
    """Get current user from session"""
    if not hasattr(request, 'session') or not request.session.get("user"):
        raise HTTPException(status_code=302, detail="Not authenticated", headers={"Location": "/login"})
    return request.session.get("user")


# Pagination class
class Paginator:
    def __init__(self, query, per_page, page):
        self.query = query
        self.per_page = per_page
        self.page = page
        self.total = query.count()
        self.pages = math.ceil(self.total / per_page) if per_page > 0 else 1
        self.has_previous = page > 1
        self.has_next = page < self.pages
        self.previous_page_number = page - 1 if self.has_previous else None
        self.next_page_number = page + 1 if self.has_next else None
        self.page_range = list(range(1, self.pages + 1))

    def get_page(self):
        offset = (self.page - 1) * self.per_page
        return self.query.offset(offset).limit(self.per_page).all()


@ElevenLabsWebRouter.get("/call_history", response_class=HTMLResponse)
async def elevenlabs_call_history(request: Request, page: int = 1, agent_id: str = None):
    """ElevenLabs call history page with pagination and agent filtering"""
    try:
        # Get current user
        user = get_current_user(request)
        user_id = user.get("user_id")
        
        # Get user's agents
        user_agents = db.session.query(AgentModel).filter(AgentModel.created_by == user_id).all()
        agent_ids = [agent.id for agent in user_agents]
        
        if not agent_ids:
            # No agents found - return empty page
            context = {
                "request": request,
                "user": user,
                "audio_recordings": [],
                "page_obj": Paginator(db.session.query(AudioRecordings).filter(False), 10, 1),  # Empty paginator
                "agents": [],
                "agent_id": agent_id,
                "host": f"{request.url.scheme}://{request.headers.get('host')}"
            }
            return templates.TemplateResponse("ElevenLabs_Integration/web/call_history.html", context)
        
        # Build query for call history
        query = db.session.query(AudioRecordings).options(
            joinedload(AudioRecordings.call_relation),
            joinedload(AudioRecordings.agent)
        ).filter(
            AudioRecordings.agent_id.in_(agent_ids)
        )
        
        # Filter by specific agent if requested
        if agent_id:
            try:
                agent_id_int = int(agent_id)
                if agent_id_int in agent_ids:  # Ensure user owns this agent
                    query = query.filter(AudioRecordings.agent_id == agent_id_int)
                else:
                    # User doesn't own this agent - redirect to general call history
                    return RedirectResponse(url="/elevenlabs/call_history", status_code=302)
            except ValueError:
                # Invalid agent_id format - ignore filter
                pass
        
        # Order by creation date (newest first)
        query = query.order_by(desc(AudioRecordings.created_at))
        
        # Paginate results
        paginator = Paginator(query, 10, page)
        audio_recordings = paginator.get_page()
        
        # Prepare context
        context = {
            "request": request,
            "user": user,
            "audio_recordings": audio_recordings,
            "page_obj": paginator,
            "agents": user_agents,
            "agent_id": agent_id,
            "host": f"{request.url.scheme}://{request.headers.get('host')}"
        }
        
        return templates.TemplateResponse("ElevenLabs_Integration/web/call_history.html", context)
        
    except HTTPException as he:
        if he.status_code == 302:
            return RedirectResponse(url="/login", status_code=302)
        raise he
    except Exception as e:
        logger.error(f"Error in ElevenLabs call history: {e}")
        raise HTTPException(status_code=500, detail="Failed to load call history")


@ElevenLabsWebRouter.get("/test-preview", response_class=HTMLResponse)
async def test_preview(request: Request):
    """Test endpoint to debug preview issues"""
    agent_id = request.query_params.get("agent_id", "test")
    return HTMLResponse(f"<h1>Test Preview Works! Agent ID: {agent_id}</h1>")

@ElevenLabsWebRouter.get("/preview_agent", response_class=HTMLResponse)
async def preview_elevenlabs_agent(request: Request):
    """
    Preview endpoint for ElevenLabs agents - simplified for debugging
    """
    agent_id = request.query_params.get("agent_id", "test")
    host = f"{request.url.scheme}://{request.headers.get('host')}"
    
    html_content = f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css">
        <link rel="stylesheet" href="{host}/static/Web/css/bot_style.css">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Agent Preview</title>
    </head>
    <body>
        <script src="{host}/elevenlabs/preview/v1/chatbot-script-preview.js/{agent_id}"></script>
        <script src="{host}/static/js/elevenlabs_websocket.js"></script>
        <input hidden id="username" placeholder="Username" value="admin">
        <input hidden id="password" placeholder="Password" value="admin123">
        <input hidden id="elevenlabs-agent-id" value="{agent_id}">
    </body>
    </html>
    '''
    
    return HTMLResponse(content=html_content)


@ElevenLabsWebRouter.get("/chatbot-script-preview.js/{agent_id}")
def elevenlabs_chatbot_script(request: Request, agent_id: str):
    """
    Dynamic JavaScript injection for ElevenLabs agents - mirrors app folder chatbot script
    """
    try:
        ws_protocol = "wss" if request.url.scheme == "https" else "ws"
        agent = AgentModel.get_by_dynamic_id(agent_id)

        if not agent:
            response = Response("// Agent not found.", media_type="application/javascript")
            response.headers['Cache-Control'] = 'public, max-age=3600'
            return response

        if not agent.elvn_lab_agent_id:
            response = Response("// ElevenLabs agent ID not configured.", media_type="application/javascript")
            response.headers['Cache-Control'] = 'public, max-age=3600'
            return response

        created_by = agent.created_by
        domain = request.base_url.hostname
        domains = os.getenv("DOMAIN_NAME", "").split(",")
        host = os.getenv("HOST", str(request.base_url))
        
        # Get agent appearance settings
        appearances = AgentConnectionModel.get_by_agent_id(agent.id)
        if not appearances:
            # Set default appearance
            appearances = type('obj', (object,), {
                'primary_color': '#00d4ff',
                'secondary_color': '#006eff', 
                'pulse_color': 'rgba(0, 212, 255, 0.3)',
                'icon_url': f'{host}/static/Web/images/gif-icon-1.gif',
                'widget_size': 'medium',
                'start_btn_color': '#1a1a1a',
            })
        else:
            # Ensure all required properties exist
            if not hasattr(appearances, 'primary_color') or not appearances.primary_color:
                appearances.primary_color = '#00d4ff'
            if not hasattr(appearances, 'secondary_color') or not appearances.secondary_color:
                appearances.secondary_color = '#006eff'
            if not hasattr(appearances, 'pulse_color') or not appearances.pulse_color:
                appearances.pulse_color = 'rgba(0, 212, 255, 0.3)'
            if not hasattr(appearances, 'icon_url') or not appearances.icon_url:
                appearances.icon_url = f'{host}/static/Web/images/gif-icon-1.gif'
            if not hasattr(appearances, 'widget_size') or not appearances.widget_size:
                appearances.widget_size = 'medium'
            if not hasattr(appearances, 'start_btn_color') or not appearances.start_btn_color:
                appearances.start_btn_color = '#1a1a1a'
        
        # Calculate widget sizes based on widget_size setting
        size_settings = {
            'small': {
                'panel_padding': '15px',
                'panel_gap': '10px',
                'panel_min_width': '220px',
                'indicator_size': '40px',
                'button_padding': '8px 15px',
                'button_font_size': '12px',
                'language_btn_padding': '8px 12px',
                'language_panel_min_width': '220px'
            },
            'medium': {
                'panel_padding': '20px',
                'panel_gap': '15px',
                'panel_min_width': '280px',
                'indicator_size': '50px',
                'button_padding': '12px 20px',
                'button_font_size': '14px',
                'language_btn_padding': '10px 14px',
                'language_panel_min_width': '280px'
            },
            'large': {
                'panel_padding': '25px',
                'panel_gap': '20px',
                'panel_min_width': '320px',
                'indicator_size': '60px',
                'button_padding': '15px 25px',
                'button_font_size': '16px',
                'language_btn_padding': '12px 16px',
                'language_panel_min_width': '320px'
            }
        }
        
        current_size = size_settings.get(appearances.widget_size, size_settings['medium'])
        
        # Check limits and user status
        user = UserModel.get_by_id(created_by) if created_by else None
        daily_call_limit = DailyCallLimitModel.get_by_agent_id(agent.id)
        overall_token_limit = OverallTokenLimitModel.get_by_agent_id(agent.id)
        
        # Check domain approval
        approved_domain = ApprovedDomainModel.check_domain_exists(domain, created_by) if created_by else True
        
        if not (approved_domain or domain in domains):
            script_content = f'''
            console.error("Domain not approved for agent: {agent_id}");
            alert("This domain is not approved to use this agent.");
            '''
            response = Response(script_content, media_type="application/javascript")
            response.headers['Cache-Control'] = 'no-cache'
            return response

        # Check user token limits
        if user and int(user.tokens) == 0:
            script_content = f'''
            document.addEventListener('DOMContentLoaded', function() {{
                (function() {{
                    
                    
                    const popup = document.createElement('div');
                    popup.className = 'elevenlabs-popup';
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
                        z-index: 10000;
                    `;
                    
                    popup.innerHTML = `
                        <h2 style="font-size: 24px; margin-bottom: 10px;">🎯 Need More Tokens?</h2>
                        <p style="font-size: 18px; margin-bottom: 20px;">Get extra tokens now and keep enjoying premium ElevenLabs AI features!</p>
                        <a href="{host}/payment" style="
                            background: #fff;
                            color: #0C7FDA;
                            padding: 10px 20px;
                            font-size: 18px;
                            font-weight: bold;
                            border: none;
                            border-radius: 6px;
                            cursor: pointer;
                            text-decoration: none;
                            display: inline-block;
                            transition: background 0.3s;">
                            Buy Tokens Now
                        </a>
                    `;
                    
                    document.body.appendChild(popup);
                    setTimeout(() => {{ popup.style.display = 'block'; }}, 1000);
                }})();
            }});
            '''
            
        elif overall_token_limit and int(overall_token_limit.last_used_tokens) >= int(overall_token_limit.overall_token_limit):
            script_content = f'''
            document.addEventListener('DOMContentLoaded', function() {{
                (function() {{
                   
                    
                    const popup = document.createElement('div');
                    popup.className = 'elevenlabs-popup';
                    popup.style.cssText = `
                        background: linear-gradient(135deg, #ff6b6b, #feca57);
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
                        z-index: 10000;
                    `;
                    
                    popup.innerHTML = `
                        <h2 style="font-size: 24px; margin-bottom: 10px;">⚠️ Token Limit Reached</h2>
                        <p style="font-size: 18px; margin-bottom: 20px;">Upgrade now to unlock unlimited tokens and access all premium ElevenLabs features!</p>
                        <a href="{host}/update_agent?agent_id={agent.id}" style="
                            background: #fff;
                            color: #ff6b6b;
                            padding: 10px 20px;
                            font-size: 18px;
                            font-weight: bold;
                            border: none;
                            border-radius: 6px;
                            cursor: pointer;
                            text-decoration: none;
                            display: inline-block;
                            transition: background 0.3s;">
                            Upgrade Plan
                        </a>
                    `;
                    
                    document.body.appendChild(popup);
                    setTimeout(() => {{ popup.style.display = 'block'; }}, 1000);
                }})();
            }});
            '''
            
        elif daily_call_limit and int(daily_call_limit.set_value) <= int(daily_call_limit.last_used):
            script_content = f'''
            document.addEventListener('DOMContentLoaded', function() {{
                (function() {{
                   
                    
                    const popup = document.createElement('div');
                    popup.className = 'elevenlabs-popup';
                    popup.style.cssText = `
                        background: linear-gradient(135deg, #fd79a8, #fdcb6e);
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
                        z-index: 10000;
                    `;
                    
                    popup.innerHTML = `
                        <h2 style="font-size: 24px; margin-bottom: 10px;">📅 Daily Limit Reached</h2>
                        <p style="font-size: 18px; margin-bottom: 20px;">You've reached your daily call limit. Please update your plan to continue using ElevenLabs.</p>
                        <a href="{host}/update_agent?agent_id={agent.id}" style="
                            background: #fff;
                            color: #fd79a8;
                            padding: 10px 20px;
                            font-size: 18px;
                            font-weight: bold;
                            border: none;
                            border-radius: 6px;
                            cursor: pointer;
                            text-decoration: none;
                            display: inline-block;
                            transition: background 0.3s;">
                            Update Plan
                        </a>
                    `;
                    
                    document.body.appendChild(popup);
                    setTimeout(() => {{ popup.style.display = 'block'; }}, 1000);
                }})();
            }});
            '''
        
        else:
            # Always show Enhanced ElevenLabs widget with custom design (removed checkbox logic)
            script_content = f'''
            document.addEventListener('DOMContentLoaded', function() {{
                (function() {{
                    
                    
                    // Inject ElevenLabs WebSocket script
                    const elevenLabsScript = document.createElement('script');
                    elevenLabsScript.src = "{host}/static/js/elevenlabs_websocket.js";
                    document.head.appendChild(elevenLabsScript);
                    
                    elevenLabsScript.onload = function() {{
                        if (typeof ElevenLabsWebSocketClient === 'function') {{
                          
                            // Don't auto-initialize, wait for user interaction
                            window.elevenLabsAgentId = '{agent_id}';
                            window.elevenLabsLanguage = '{agent.selected_language or "en"}'; // Use agent's selected language
                           
                        }} else {{
                            console.error("ElevenLabsWebSocketClient is not defined");
                        }}
                    }};

                    // Create enhanced ElevenLabs widget with ElevenLabs official design
                        const container = document.createElement('div');
                        container.innerHTML = `
                            <!-- ElevenLabs Agent Widget -->
                            <div id="elevenlabs-widget" style="
                                position: fixed;
                                bottom: 20px;
                                right: 20px;
                                z-index: 10000;
                                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                            ">
                                <!-- Main Control Panel -->
                                <div id="main-panel" style="
                                    background: white;
                                    border-radius: 20px;
                                    padding: {current_size['panel_padding']};
                                    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.15);
                                    display: flex;
                                    align-items: center;
                                    gap: {current_size['panel_gap']};
                                    min-width: {current_size['panel_min_width']};
                                    border: 1px solid rgba(0, 0, 0, 0.05);
                                    position: relative;
                                ">
                                    <!-- Voice Indicator -->
                                    <div id="voice-indicator" style="
                                        width: {current_size['indicator_size']};
                                        height: {current_size['indicator_size']};
                                        border-radius: 12px;
                                        background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color});
                                        display: flex;
                                        align-items: center;
                                        justify-content: center;
                                        flex-shrink: 0;
                                        transition: all 0.3s ease;
                                        position: relative;
                                        overflow: hidden;
                                    ">
                                        <div style="
                                            width: 20px;
                                            height: 20px;
                                            border-radius: 4px;
                                            background: rgba(255,255,255,0.9);
                                            animation: pulse 2s infinite;
                                        "></div>
                                    </div>
                                    
                                    <!-- Action Button -->
                                    <button id="voice-chat-btn" onclick="toggleElevenLabsChat()" style="
                                        background: {appearances.start_btn_color};
                                        color: white;
                                        border: none;
                                        border-radius: 18px;
                                        padding: {current_size['button_padding']};
                                        font-weight: 700;
                                        font-size: {current_size['button_font_size']};
                                        letter-spacing: 0.5px;
                                        cursor: pointer;
                                        transition: all 0.3s ease;
                                        display: flex;
                                        align-items: center;
                                        gap: 10px;
                                        flex: 1;
                                        text-transform: uppercase;
                                        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
                                    ">
                                        <i class="fas fa-microphone" style="font-size: {current_size['button_font_size']};"></i>
                                        <span id="btn-text">START CHAT</span>
                                    </button>
                                    
                                    <!-- Language Selector -->
                                    <button id="language-btn" onclick="toggleLanguagePanel()" style="
                                        background: #f8f9fa;
                                        border: 1px solid #e1e5e9;
                                        border-radius: 15px;
                                        padding: {current_size['language_btn_padding']};
                                        cursor: pointer;
                                        display: flex;
                                        align-items: center;
                                        gap: 8px;
                                        transition: all 0.2s ease;
                                        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
                                    " data-language="{agent.selected_language or 'en'}">
                                        <span id="selected-flag" style="font-size: 16px;">🇺🇸</span>
                                        <i class="fas fa-chevron-down" style="font-size: 8px; color: #6c757d; transition: transform 0.2s ease;"></i>
                                    </button>
                                    
                                    <!-- Language Selection Panel (positioned relative to main panel) -->
                                    <div id="language-panel" style="
                                        background: white;
                                        border-radius: 20px;
                                        padding: 20px;
                                        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
                                        display: none;
                                        min-width: {current_size['language_panel_min_width']};
                                        position: absolute;
                                        bottom: calc(100% + 10px);
                                        right: 0;
                                        z-index: 10001;
                                        border: 1px solid rgba(0, 0, 0, 0.05);
                                        max-height: 400px;
                                        overflow-y: auto;
                                    ">
                                        <div style="margin-bottom: 15px;">
                                        <div style="margin-bottom: 15px;">
                                            <!-- Top 10 Most Spoken Languages -->
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('en', '🇺🇸', 'English')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇺🇸</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">English</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('zh', '🇨🇳', 'Chinese')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇨🇳</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Chinese</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('hi', '🇮🇳', 'Hindi')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇮🇳</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Hindi</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('es', '🇪🇸', 'Spanish')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇪🇸</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Spanish</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('fr', '🇫🇷', 'French')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇫🇷</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">French</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('ar', '🇸🇦', 'Arabic')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇸🇦</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Arabic</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('ru', '🇷🇺', 'Russian')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇷🇺</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Russian</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('pt', '🇵🇹', 'Portuguese')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇵🇹</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Portuguese</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('id', '🇮🇩', 'Indonesian')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇮🇩</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Indonesian</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('de', '🇩🇪', 'German')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇩🇪</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">German</span>
                                            </div>
                                            <div style="display: flex; align-items: center; padding: 10px; cursor: pointer; border-radius: 12px; transition: background 0.2s;" onclick="selectLanguage('ta', '🇮🇳', 'Tamil')">
                                                <span style="font-size: 20px; margin-right: 12px;">🇮🇳</span>
                                                <span style="font-weight: 600; color: #1a1a1a;">Tamil</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- Branding -->
                                <div style="
                                    text-align: center;
                                    margin-top: 12px;
                                    font-size: 10px;
                                    color: #adb5bd;
                                    opacity: 0.8;
                                    font-weight: 500;
                                ">
                                    Powered by VoiceNinja
                                </div>
                            </div>
                        `;
                        document.body.appendChild(container);

                        // Add ElevenLabs-specific control functions
                        window.isConnected = false;
                        window.selectedLanguage = '{agent.selected_language or "en"}';
                        console.log('Agent selected language from database:', '{agent.selected_language or "en"}');
                        
                        // Language to flag mapping
                        window.languageFlags = {{
                            'en': '🇺🇸',
                            'zh': '🇨🇳',
                            'hi': '🇮🇳',
                            'es': '🇪🇸',
                            'fr': '🇫🇷',
                            'ar': '🇸🇦',
                            'ru': '🇷🇺',
                            'pt': '🇵🇹',
                            'id': '🇮🇩',
                            'de': '🇩🇪',
                            'ta': '🇮🇳'
                        }};
                        
                        // Initialize flag display based on agent's selected language
                        const initialFlag = window.languageFlags[window.selectedLanguage] || '🇺🇸';
                        document.getElementById('selected-flag').innerText = initialFlag;
                        console.log('Initial language set to:', window.selectedLanguage, 'with flag:', initialFlag);
                       // Toggle language panel
                        window.toggleLanguagePanel = function() {{
                            const panel = document.getElementById('language-panel');
                            const btn = document.getElementById('language-btn');
                            const chevron = btn.querySelector('.fa-chevron-down');
                            
                            if (panel.style.display === 'none' || panel.style.display === '') {{
                                panel.style.display = 'block';
                                chevron.style.transform = 'rotate(180deg)';
                                
                            }} else {{
                                panel.style.display = 'none';
                                chevron.style.transform = 'rotate(0deg)';
                                
                            }}
                        }};
                        
                        // Language selection function
                        window.selectLanguage = function(code, flag, name) {{
                            console.log('Language selected:', code, flag, name);
                            window.selectedLanguage = code;
                            document.getElementById('selected-flag').innerText = flag;
                            document.getElementById('language-panel').style.display = 'none';
                            
                            // Reset chevron rotation
                            const chevron = document.getElementById('language-btn').querySelector('.fa-chevron-down');
                            chevron.style.transform = 'rotate(0deg)';
                            
                            // Determine appropriate model based on language
                            const ENGLISH_CODES = ["en", "en-US", "en-GB"];
                            const EN_MODELS = ["eleven_turbo_v2", "eleven_flash_v2"];
                            const NON_EN_MODELS = ["eleven_turbo_v2_5", "eleven_flash_v2_5"];
                            
                            let selectedModel;
                            if (ENGLISH_CODES.includes(code)) {{
                                selectedModel = "eleven_turbo_v2";
                            }} else {{
                                selectedModel = "eleven_turbo_v2_5";
                            }}
                            
                            // Update client language if already connected
                            if (window.elevenLabsClient && window.elevenLabsClient.ws && window.elevenLabsClient.ws.readyState === WebSocket.OPEN) {{
                                console.log('Sending language update to WebSocket:', code, 'with model:', selectedModel);
                                window.elevenLabsClient.ws.send(JSON.stringify({{
                                    type: 'conversation_init',
                                    language: code,
                                    model: selectedModel
                                }}));
                            }} else if (window.elevenLabsClient) {{
                                // Update the client's language property for future connections
                                window.elevenLabsClient.language = code;
                                console.log('Updated client language property to:', code);
                            }}
                            
                           
                        }};
                        
                        // Main chat toggle function
                        window.toggleElevenLabsChat = function() {{
                            if (!window.isConnected) {{
                                // Start connection
                                document.getElementById('btn-text').innerText = 'CONNECTING...';
                                document.getElementById('voice-chat-btn').style.background = '#666';
                                
                                // Initialize ElevenLabs client when user first clicks
                                if (!window.elevenLabsClient && window.elevenLabsAgentId) {{
                                    // Get selected language or default to English
                                    const selectedLanguage = window.selectedLanguage || window.elevenLabsLanguage || 'en';
                                    
                                    // Determine appropriate model based on language
                                    const ENGLISH_CODES = ["en", "en-US", "en-GB"];
                                    let selectedModel;
                                    if (ENGLISH_CODES.includes(selectedLanguage)) {{
                                        selectedModel = "eleven_turbo_v2";
                                    }} else {{
                                        selectedModel = "eleven_turbo_v2_5";
                                    }}
                                    
                                    console.log('Initializing ElevenLabs client with language:', selectedLanguage, 'and model:', selectedModel);
                                    window.elevenLabsClient = new ElevenLabsWebSocketClient(window.elevenLabsAgentId, selectedLanguage, selectedModel);
                                }}
                                
                                // Start ElevenLabs connection
                                if (window.elevenLabsClient) {{
                                    
                                    window.elevenLabsClient.connect().then(() => {{
                                        // Connection successful
                                        window.isConnected = true;
                                        document.getElementById('btn-text').innerHTML = '<i class="fas fa-times" style="font-size: 14px;"></i> END CALL';
                                        document.getElementById('voice-chat-btn').style.background = '#000';
                                        
                                        // Animate voice indicator
                                        const indicator = document.getElementById('voice-indicator');
                                        indicator.style.animation = 'pulse 2s infinite';
                                        indicator.style.background = 'linear-gradient(45deg, #00ff88, #00cc66)';
                                        
                                       
                                        
                                        // Listen for disconnection
                                        window.elevenLabsClient.ws.onclose = function() {{
                                            window.isConnected = false;
                                            document.getElementById('btn-text').innerHTML = 'VOICE CHAT';
                                            document.getElementById('voice-chat-btn').style.background = '{appearances.start_btn_color}';
                                            
                                            // Reset voice indicator
                                            const indicator = document.getElementById('voice-indicator');
                                            indicator.style.animation = 'none';
                                            indicator.style.background = '{appearances.pulse_color}';
                                            
                                            
                                        }};
                                        
                                    }}).catch(err => {{
                                        console.error("Failed to connect to ElevenLabs:", err);
                                        document.getElementById('btn-text').innerText = 'CONNECTION FAILED';
                                        document.getElementById('voice-chat-btn').style.background = '#ff4444';
                                        setTimeout(() => {{
                                            document.getElementById('btn-text').innerHTML = '<i class="fas fa-phone" style="font-size: 14px;"></i> VOICE CHAT';
                                            document.getElementById('voice-chat-btn').style.background = '{appearances.start_btn_color}';
                                        }}, 3000);
                                    }});
                                }}
                            }} else {{
                                // End connection
                                if (window.elevenLabsClient) {{
                                    window.elevenLabsClient.disconnect();
                                    // UI will be updated by the onclose handler
                                }}
                            }}
                        }};
                        
                        // Add CSS animations
                        const style = document.createElement('style');
                        style.textContent = `
                            @keyframes pulse {{
                                0% {{ opacity: 1; transform: scale(1); }}
                                50% {{ opacity: 0.7; transform: scale(0.95); }}
                                100% {{ opacity: 1; transform: scale(1); }}
                            }}
                            
                            #elevenlabs-widget button:hover {{
                                transform: translateY(-1px);
                                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                            }}
                            
                            #language-panel div:hover {{
                                background: #f8f9fa !important;
                            }}
                            
                            #voice-chat-btn:active {{
                                transform: translateY(0);
                            }}
                        `;
                        document.head.appendChild(style);
                        
                        // Close language panel when clicking outside
                        document.addEventListener('click', function(event) {{
                            const languagePanel = document.getElementById('language-panel');
                            const languageBtn = document.getElementById('language-btn');
                            
                            if (!languagePanel.contains(event.target) && !languageBtn.contains(event.target)) {{
                                languagePanel.style.display = 'none';
                            }}
                        }});
                        
                        document.body.appendChild(container);
                    }})();
                }});
                '''

        response = Response(script_content, media_type="application/javascript")
        response.headers['Cache-Control'] = 'no-cache'
        return response
        
    except Exception as e:
        logger.error(f"Error generating ElevenLabs chatbot script: {e}")
        error_script = f'''
        console.error("Failed to load ElevenLabs agent script: {str(e)}");
        alert("Failed to load ElevenLabs agent. Please try again.");
        '''
        response = Response(error_script, media_type="application/javascript")
        response.headers['Cache-Control'] = 'no-cache'
        return response


@ElevenLabsWebRouter.get("/health")
async def elevenlabs_web_health():
    """Health check for ElevenLabs web integration"""
    return {
        "status": "healthy",
        "service": "ElevenLabs Web Integration",
        "features": ["preview", "dynamic_scripts", "ui_injection"]
    }