from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from app.databases.models import (
    AgentModel, 
    UserModel, 
    ApprovedDomainModel,
    DailyCallLimitModel,
    OverallTokenLimitModel,
    AgentConnectionModel
)
import os
from loguru import logger

ElevenLabsWebRouter = APIRouter(prefix="/elevenlabs/web", tags=["elevenlabs-web"])

# Setup templates
templates = Jinja2Templates(directory="templates")


@ElevenLabsWebRouter.get("/preview_agent", response_class=HTMLResponse)
async def preview_elevenlabs_agent(request: Request):
    """
    Preview endpoint for ElevenLabs agents - mirrors the app folder preview functionality
    """
    try:
        agent_id = request.query_params.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=400, detail="agent_id parameter required")
            
        # Get user session (if available) - for preview, we might allow without session
        user_id = None
        if hasattr(request, 'session') and request.session.get("user"):
            user_id = request.session.get("user").get("user_id")
        
        scheme = request.url.scheme
        host = f"{scheme}://{request.headers.get('host')}"
        domain = request.base_url.hostname
        domains = os.getenv("DOMAIN_NAME", "").split(",")
        
        # Check domain approval if user_id is available
        approved_domain = None
        if user_id:
            approved_domain = ApprovedDomainModel.check_domain_exists(domain, user_id)
        
        # Allow preview for approved domains or configured domains
        if approved_domain or domain in domains or not user_id:  # Allow if no session for preview
            context = {
                "request": request, 
                "agent_id": agent_id, 
                "host": host,
                "integration_type": "elevenlabs"
            }
            return templates.TemplateResponse("elevenlabs_testing.html", context)
        else:
            return HTTPException(status_code=403, detail="Domain not approved")
            
    except Exception as e:
        logger.error(f"Error in ElevenLabs preview: {e}")
        raise HTTPException(status_code=500, detail="Failed to load preview")


@ElevenLabsWebRouter.get("/chatbot-script.js/{agent_id}")
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
                'primary_color': '#0C7FDA',
                'secondary_color': '#99d2ff', 
                'pulse_color': '#ffffff',
                'icon_url': f'{host}/static/Web/images/default_voice_icon.png'
            })
        
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
                    console.log("ElevenLabs Agent - No tokens available");
                    
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
                    console.log("ElevenLabs Agent - Overall token limit reached");
                    
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
                    console.log("ElevenLabs Agent - Daily call limit reached");
                    
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
            # Check if design mode is enabled for enhanced widget
            if agent.is_design_enabled:
                # Enhanced ElevenLabs widget with custom design
                script_content = f'''
                document.addEventListener('DOMContentLoaded', function() {{
                    (function() {{
                        console.log("ElevenLabs Enhanced Design Mode Loading...");
                        
                        // Inject ElevenLabs WebSocket script
                        const elevenLabsScript = document.createElement('script');
                        elevenLabsScript.src = "{host}/static/js/elevenlabs_websocket.js";
                        document.head.appendChild(elevenLabsScript);
                        
                        elevenLabsScript.onload = function() {{
                            if (typeof ElevenLabsWebSocketClient === 'function') {{
                                console.log("ElevenLabs client class available for agent: {agent_id}");
                                // Don't auto-initialize, wait for user interaction
                                window.elevenLabsAgentId = '{agent_id}';
                                console.log("ElevenLabs agent ID set:", window.elevenLabsAgentId);
                            }} else {{
                                console.error("ElevenLabsWebSocketClient is not defined");
                            }}
                        }};

                        // Create enhanced ElevenLabs widget with same design as main app
                        const container = document.createElement('div');
                        container.innerHTML = `
                            <div class="voice_icon" onclick="toggleElevenLabsRecorder()" id="elevenLabsStartCall" 
                                style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                <img src="{appearances.icon_url}" alt="voice_icon">
                            </div>
                            <div id="elevenLabsRecorderControls" class="recorder-controls hidden" 
                                style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                <div class="settings">
                                    <div id="colorPalette" class="color-palette">
                                        <div class="color-option" 
                                            style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                        </div>
                                    </div>
                                </div>
                                <h1 id="elevenlabs-status-text">Connect with me</h1>
                                <div class="status-indicator">
                                    <img src="{host}/static/Web/images/wave.gif" alt="voice_icon">
                                </div>
                                <button onclick="stopElevenLabsRecorder()" id="elevenLabsEndCall" 
                                        style="background: linear-gradient(45deg, {appearances.primary_color}, {appearances.secondary_color}, {appearances.pulse_color});">
                                    Stop Recording
                                </button>
                                <div id="elevenlabs-connection-status" style="margin-top: 10px; font-size: 0.9em;">Ready</div>
                                <div id="elevenlabs-transcript" style="margin-top: 10px; max-height: 100px; overflow-y: auto; font-size: 0.8em;"></div>
                            </div>
                        `;
                        document.body.appendChild(container);

                        // Add ElevenLabs-specific control functions
                        window.toggleElevenLabsRecorder = function() {{
                            const recorderControls = document.getElementById("elevenLabsRecorderControls");
                            const startCall = document.getElementById("elevenLabsStartCall");

                            if (recorderControls.classList.contains("hidden")) {{
                                recorderControls.classList.remove("hidden");
                                recorderControls.classList.add("show");
                                startCall.style.display = "none";
                                
                                // Initialize ElevenLabs client when user first clicks
                                if (!window.elevenLabsClient && window.elevenLabsAgentId) {{
                                    console.log("Creating ElevenLabs client for agent:", window.elevenLabsAgentId);
                                    window.elevenLabsClient = new ElevenLabsWebSocketClient(window.elevenLabsAgentId);
                                }}
                                
                                // Start ElevenLabs connection
                                if (window.elevenLabsClient) {{
                                    console.log("Starting ElevenLabs connection...");
                                    window.elevenLabsClient.connect().catch(err => {{
                                        console.error("Failed to connect to ElevenLabs:", err);
                                        alert("Failed to connect to ElevenLabs: " + err.message);
                                    }});
                                }}
                            }} else {{
                                recorderControls.classList.remove("show");
                                recorderControls.classList.add("hidden");
                                startCall.style.display = "block";
                            }}
                        }};

                        window.stopElevenLabsRecorder = function() {{
                            const recorderControls = document.getElementById('elevenLabsRecorderControls');
                            const voiceIcon = document.getElementById('elevenLabsStartCall');

                            recorderControls.classList.remove('show');
                            recorderControls.classList.add('hidden');
                            voiceIcon.style.display = 'block';
                            
                            // Disconnect ElevenLabs
                            if (window.elevenLabsClient) {{
                                window.elevenLabsClient.disconnect();
                            }}
                        }};
                        `;
                        document.body.appendChild(container);
                    }})();
                }});
                '''
            else:
                # Standard ElevenLabs widget with simple design (same as main app default)
                script_content = f'''
                document.addEventListener('DOMContentLoaded', function() {{
                    (function() {{
                        console.log("ElevenLabs Standard Mode Loading...");
                        
                        // Inject ElevenLabs WebSocket script
                        const elevenLabsScript = document.createElement('script');
                        elevenLabsScript.src = "{host}/static/js/elevenlabs_websocket.js";
                        document.head.appendChild(elevenLabsScript);
                        
                        elevenLabsScript.onload = function() {{
                            if (typeof ElevenLabsWebSocketClient === 'function') {{
                                console.log("ElevenLabs client class available for agent: {agent_id}");
                                // Don't auto-initialize, wait for user interaction
                                window.elevenLabsAgentId = '{agent_id}';
                                console.log("ElevenLabs agent ID set:", window.elevenLabsAgentId);
                            }} else {{
                                console.error("ElevenLabsWebSocketClient is not defined");
                            }}
                        }};

                        // Add CSS styles (same as main app)
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

                        // Create simple widget container (same as main app)
                        const container = document.createElement('div');
                        container.className = 'phone_numder_outer';
                        container.innerHTML = `
                            <div class="phone_numder_msg" id="messageBox">
                                <div class="close_msg">
                                    <img src="https://snakescript.com/svg_ai_voice_agent/close_msg.svg" class="img-fluid" style="cursor: pointer;" onclick="document.getElementById('messageBox').style.display='none'">
                                </div>
                                <h2><span>Hello 👋</span>
                                I am your ElevenLabs AI agent.
                                <span>Let's Talk!</span>
                                </h2>
                            </div>
                            <div class="whatsapp_outer_mobile">
                                <span class="micro" id="elevenLabsStartCall">
                                <img src="https://snakescript.com/images_ai_voice_agent/microphone.svg" class="img-fluid" style="cursor: pointer;">
                                </span>
                            </div>
                        `;
                        document.body.appendChild(container);

                        // Add click handler for close button
                        document.querySelector('.close_msg img').addEventListener('click', function() {{
                            document.querySelector('.phone_numder_msg').style.display = 'none';
                        }});

                        // Add call popup HTML (same as main app)
                        const callPopup = document.createElement('div');
                        callPopup.id = 'callPopup';
                        callPopup.className = 'call-popup';
                        callPopup.innerHTML = `
                            <div class="popup-content">
                                <div class="popup-header">
                                    <div class="app-title">
                                        <img src="https://snakescript.com/images_ai_voice_agent/user.png" alt="ElevenLabs AI" style="height:38px" />
                                        ElevenLabs AI
                                    </div>
                                    <button type="button" id="closePopup" class="close-btn">
                                        <svg fill="#ffffff" height="15px" width="15px" version="1.1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 490 490">
                                            <polygon points="456.851,0 245,212.564 33.149,0 0.708,32.337 212.669,245.004 0.708,457.678 33.149,490 245,277.443 456.851,490 489.292,457.678 277.331,245.004 489.292,32.337"/>
                                        </svg>
                                    </button>
                                </div>
                                <div class="popup-body">
                                    <div class="brain-container text-center">
                                        <h3 id="elevenlabs-status-text">Say something..</h3>
                                    </div>
                                    <div class="whatsapp_outer_mobile">
                                        <span class="micro" id="startCallInPopup">
                                        <img src="https://snakescript.com/images_ai_voice_agent/microphone.svg" class="img-fluid" style="cursor: pointer;">
                                        </span>
                                    </div>
                                </div>
                                <div id="elevenlabs-transcript" class="conversation-log" style="display:none;"></div>
                                <div class="text-center mb-4">
                                    <button id="elevenLabsEndCall" class="end-call-btn">
                                        <svg fill="#ffffff" height="11px" width="11px" version="1.1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 490 490">
                                            <polygon points="456.851,0 245,212.564 33.149,0 0.708,32.337 212.669,245.004 0.708,457.678 33.149,490 245,277.443 456.851,490 489.292,457.678 277.331,245.004 489.292,32.337"/>
                                        </svg> 
                                        End Call
                                    </button>
                                </div>
                                <div id="elevenlabs-connection-status" style="margin: 10px; font-size: 0.9em; text-align: center;">Ready</div>
                            </div>
                        `;
                        document.body.appendChild(callPopup);

                        // Add event listeners
                        document.getElementById('elevenLabsStartCall').addEventListener('click', function() {{
                            document.getElementById('callPopup').style.display = 'block';
                            
                            // Initialize ElevenLabs client when user first clicks
                            if (!window.elevenLabsClient && window.elevenLabsAgentId) {{
                                console.log("Creating ElevenLabs client for agent:", window.elevenLabsAgentId);
                                window.elevenLabsClient = new ElevenLabsWebSocketClient(window.elevenLabsAgentId);
                            }}
                            
                            // Start ElevenLabs connection when popup opens
                            if (window.elevenLabsClient) {{
                                console.log("Starting ElevenLabs connection...");
                                window.elevenLabsClient.connect().catch(err => {{
                                    console.error("Failed to connect to ElevenLabs:", err);
                                    alert("Failed to connect to ElevenLabs: " + err.message);
                                }});
                            }}
                        }});

                        document.getElementById('closePopup').addEventListener('click', function() {{
                            document.getElementById('callPopup').style.display = 'none';
                            if (window.elevenLabsClient) {{
                                window.elevenLabsClient.disconnect();
                            }}
                        }});

                        document.getElementById('elevenLabsEndCall').addEventListener('click', function() {{
                            document.getElementById('callPopup').style.display = 'none';
                            if (window.elevenLabsClient) {{
                                window.elevenLabsClient.disconnect();
                            }}
                        }});

                        // Add the same CSS styles as in main app
                        const popup_style = document.createElement('style');
                        popup_style.textContent = `
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
                                border-bottom: 1px solid #373737;
                            }}

                            .app-title {{
                                display: flex;
                                align-items: center;
                                gap: 12px;
                                font-weight: bold;
                                font-size: 18px;
                            }}

                            .close-btn {{
                                background: transparent;
                                border: none;
                                cursor: pointer;
                                padding: 5px;
                            }}

                            .end-call-btn {{
                                background: #ff4757;
                                color: white;
                                border: none;
                                padding: 12px 24px;
                                border-radius: 8px;
                                font-size: 16px;
                                font-weight: bold;
                                cursor: pointer;
                                display: flex;
                                align-items: center;
                                gap: 8px;
                                transition: background 0.3s;
                            }}

                            .end-call-btn:hover {{
                                background: #ff3742;
                            }}
                        `;
                        document.head.appendChild(popup_style);
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
