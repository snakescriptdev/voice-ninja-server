from fastapi import WebSocket, status, APIRouter
from app.core import logger, VoiceSettings
from app.services import RunAssistant
from typing import Dict
import secrets,uuid, json
from app.routers.bot import run_bot
from app.databases.models import AgentModel

router = APIRouter(prefix="/ws")

# Define credentials store (replace with database in production)
USERS: Dict[str, str] = {
    "admin": "admin123",  # In production, store hashed passwords
}

# Verify credentials
async def verify_credentials(credentials: str) -> bool:
    try:
        # Decode base64 credentials from WebSocket
        import base64
        decoded = base64.b64decode(credentials).decode('utf-8')
        username, password = decoded.split(':')
        
        if username in USERS and secrets.compare_digest(
            USERS[username].encode('utf-8'),
            password.encode('utf-8')
        ):
            logger.info(f"Successful authentication attempt for user: {username}")
            return True
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        return False
    logger.warning(f"Failed authentication attempt for user: {username}")
    return False


@router.websocket("/voices/")
async def websocket_endpoint(websocket: WebSocket):
    # Get authentication header
    try:
        auth_header = websocket.query_params['authorization']
        voice = websocket.query_params.get('voice')
        if voice not in VoiceSettings.ALLOWED_VOICES:
            voice = VoiceSettings.DEFAULT_VOICE
        
        if not auth_header.startswith('Basic '):
            logger.warning("Missing or invalid Authorization header")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        credentials = auth_header.split(' ')[1]
        if not await verify_credentials(credentials):
            logger.warning("Invalid credentials provided")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        else:
            logger.info("Authentication successful")
            await websocket.accept()
            uid = uuid.uuid4()
            json_data = {
                "type": "UID",
                "uid": str(uid)
            }
            await websocket.send_json(json_data)
            await RunAssistant(websocket, voice, uid)

        
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)


@router.websocket("/")
async def twilio_websocket_endpoint(websocket: WebSocket):
    # Get authentication header
    try:
        await websocket.accept()
        start_data = websocket.iter_text()
        await start_data.__anext__()
        call_data = json.loads(await start_data.__anext__())
        stream_sid = call_data["start"]["streamSid"]
        agent_id = call_data.get("start", {}).get("customParameters", {}).get("agent_id")
        agent = AgentModel.get_by_id(agent_id)
        voice = agent.selected_voice
        welcome_msg = agent.welcome_msg
        system_instruction = agent.agent_prompt
        from sqlalchemy.orm import sessionmaker
        from app.databases.models import engine
        from sqlalchemy import select
        from app.databases.models import agent_knowledge_association, KnowledgeBaseModel, KnowledgeBaseFileModel
        Session = sessionmaker(bind=engine)
        session = Session()
        result = session.execute(
                select(agent_knowledge_association).where(agent_knowledge_association.c.agent_id == agent_id)
            )
        knowledge_base_result = result.fetchone()
        knowledge_base_text = ""
        if knowledge_base_result:
            knowledge_base_id = knowledge_base_result.knowledge_base_id
            knowledge_base = KnowledgeBaseModel.get_by_id(knowledge_base_id)
            if knowledge_base:
                each_file = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
                for file in each_file:
                    knowledge_base_text += file.text_content

        voice = agent.selected_voice
        welcome_msg = agent.welcome_msg
        system_instruction = agent.agent_prompt

        print("WebSocket connection accepted")
        await run_bot(websocket, voice, stream_sid, welcome_msg, system_instruction, knowledge_base_text, agent.id)

    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)

@router.websocket("/agent_ws/")
async def agent_websocket_endpoint(websocket: WebSocket):
    # Get authentication header
    try:
        # auth_header = websocket.query_params['authorization']
        agent_id = websocket.query_params.get('agent_id')
        
        await websocket.accept()
        agent = AgentModel.get_by_id(agent_id)
        from sqlalchemy.orm import sessionmaker
        from app.databases.models import engine
        from sqlalchemy import select
        from app.databases.models import agent_knowledge_association, KnowledgeBaseModel, KnowledgeBaseFileModel
        Session = sessionmaker(bind=engine)
        session = Session()
        result = session.execute(
                select(agent_knowledge_association).where(agent_knowledge_association.c.agent_id == agent_id)
            )
        knowledge_base_result = result.fetchone()
        knowledge_base_text = ""
        if knowledge_base_result:
            knowledge_base_id = knowledge_base_result.knowledge_base_id
            knowledge_base = KnowledgeBaseModel.get_by_id(knowledge_base_id)
            if knowledge_base:
                each_file = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
                for file in each_file:
                    knowledge_base_text += file.text_content

        voice = agent.selected_voice
        welcome_msg = agent.welcome_msg
        system_instruction = agent.agent_prompt
        uid = uuid.uuid4()
        json_data = {
            "type": "UID",
            "uid": str(uid)
        }
        await websocket.send_json(json_data)
        await run_bot(websocket, voice, None, welcome_msg, system_instruction, knowledge_base_text, agent.id)

        
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)