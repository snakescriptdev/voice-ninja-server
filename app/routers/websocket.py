from fastapi import WebSocket, status, APIRouter
from app.core import logger, VoiceSettings
from app.services import RunAssistant
from typing import Dict
import secrets,uuid, json
from app.routers.bot import run_bot
from app.databases.models import AgentModel, CustomFunctionModel, DailyCallLimitModel, OverallTokenLimitModel,VoiceModel
from user_agents import parse

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
        user_agent_bytes = websocket.headers.get("user-agent", b"")
        user_agent = user_agent_bytes.decode("utf-8") if isinstance(user_agent_bytes, bytes) else user_agent_bytes

        # Parse user-agent
        parsed_ua = parse(user_agent)
        
        # Determine device type
        if parsed_ua.is_mobile:
            device_type = "Mobile"
        elif parsed_ua.is_tablet: 
            device_type = "Tablet"
        elif parsed_ua.is_pc:
            device_type = "Desktop"
        else:
            device_type = "Unknown"
        await websocket.accept()
        start_data = websocket.iter_text()
        await start_data.__anext__()
        call_data = json.loads(await start_data.__anext__())
        stream_sid = call_data["start"]["streamSid"]
        agent_id = call_data.get("start", {}).get("customParameters", {}).get("agent_id")
        user_id = call_data.get("start", {}).get("customParameters", {}).get("user_id")
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
        knowledge_base_id = None
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
        dynamic_variables = agent.dynamic_variable
        noise_setting_variables = agent.noise_setting_variable
        temperature = agent.temperature
        max_output_tokens = agent.max_output_tokens
        custom_functions = CustomFunctionModel.get_all_by_agent_id(agent_id)
        daily_call_limit = DailyCallLimitModel.get_by_agent_id(agent_id)
        overall_token_limit = OverallTokenLimitModel.get_by_agent_id(agent_id)
        per_call_token_limit = agent.per_call_token_limit if agent.per_call_token_limit else 0
        update_per_call_token_limit = agent.update_per_call_token_limit if agent.update_per_call_token_limit else 0

        if daily_call_limit and int(daily_call_limit.set_value) == int(daily_call_limit.last_used):
            logger.error(f"Daily call limit reached: {daily_call_limit.last_used}/{daily_call_limit.set_value}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        
        if overall_token_limit and int(overall_token_limit.last_used_tokens) == int(overall_token_limit.overall_token_limit):
            logger.error(f"Overall token limit reached: {overall_token_limit.last_used_tokens}/{overall_token_limit.overall_token_limit}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        
        if per_call_token_limit > 0 and update_per_call_token_limit >= per_call_token_limit:
            logger.error(f"Per call token limit reached: {update_per_call_token_limit}/{per_call_token_limit}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        
        
        custom_functions_list = []
        for function in custom_functions:
            custom_functions_list.append({
                "name": function.function_name,
                "description": function.function_description,
                "parameters": function.function_parameters
            })
        print("WebSocket connection accepted")


        voice_id = agent.selected_voice  
        voice_obj = VoiceModel.get_by_id(voice_id)
        custom_voice_id,voice = None,None

        if voice_obj.is_custom_voice:
            custom_voice_id = voice_obj.elevenlabs_voice_id
        else:
            voice = voice_obj.voice_name

        params = {
            "voice": voice,
            "stream_sid": stream_sid,
            "welcome_msg": welcome_msg,
            "system_instruction": system_instruction,
            "knowledge_base": knowledge_base_id,
            "agent_id": agent.id,
            "user_id": user_id,
            "dynamic_variables": dynamic_variables,
            "noise_setting_variables": noise_setting_variables,
            "uid": None,
            "custom_functions_list": None,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "is_custom_voice":voice_obj.is_custom_voice,
            "custom_voice_id":custom_voice_id
        }
        await run_bot(websocket, **params)

    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)

@router.websocket("/agent_ws/")
async def agent_websocket_endpoint(websocket: WebSocket):
    # Get authentication header
    try:
        await websocket.accept()
        user_agent_bytes = websocket.headers.get("user-agent", b"")
        user_agent = user_agent_bytes.decode("utf-8") if isinstance(user_agent_bytes, bytes) else user_agent_bytes

        # Parse user-agent
        parsed_ua = parse(user_agent)
        
        # Determine device type
        if parsed_ua.is_mobile:
            device_type = "Mobile"
        elif parsed_ua.is_tablet:
            device_type = "Tablet"
        elif parsed_ua.is_pc:
            device_type = "Desktop"
        else:
            device_type = "Unknown"
        agent_id = websocket.query_params.get('agent_id')
        agent = AgentModel.get_by_id(agent_id)
        user = agent.created_by
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
        knowledge_base_id = None
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
        dynamic_variables = agent.dynamic_variable
        noise_setting_variables = agent.noise_setting_variable
        temperature = agent.temperature
        max_output_tokens = agent.max_output_tokens
        custom_functions = CustomFunctionModel.get_all_by_agent_id(agent_id)
        daily_call_limit = DailyCallLimitModel.get_by_agent_id(agent_id)
        overall_token_limit = OverallTokenLimitModel.get_by_agent_id(agent_id)
        per_call_token_limit = agent.per_call_token_limit if agent.per_call_token_limit else 0
        update_per_call_token_limit = agent.update_per_call_token_limit if agent.update_per_call_token_limit else 0

        if daily_call_limit and int(daily_call_limit.set_value) == int(daily_call_limit.last_used):
            logger.error(f"Daily call limit reached: {daily_call_limit.last_used}/{daily_call_limit.set_value}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        
        if overall_token_limit and int(overall_token_limit.last_used_tokens) == int(overall_token_limit.overall_token_limit):
            logger.error(f"Overall token limit reached: {overall_token_limit.last_used_tokens}/{overall_token_limit.overall_token_limit}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        
        if per_call_token_limit > 0 and int(update_per_call_token_limit) >= per_call_token_limit:
            logger.error(f"Per call token limit reached: {update_per_call_token_limit}/{per_call_token_limit}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return


        custom_functions_list = []
        for function in custom_functions:
            custom_functions_list.append({
                "name": function.function_name,
                "description": function.function_description,
                "parameters": function.function_parameters
            })

        uid = uuid.uuid4()
        json_data = {
            "type": "UID",
            "uid": str(uid),
            "device_type": device_type,
        }
        await websocket.send_json(json_data)

        voice_id = agent.selected_voice  
        voice_obj = VoiceModel.get_by_id(voice_id)
        custom_voice_id,voice = None,None

        if voice_obj.is_custom_voice:
            custom_voice_id = voice_obj.elevenlabs_voice_id
        else:
            voice = voice_obj.voice_name

        params = {
            "voice": voice,
            "stream_sid": None,
            "welcome_msg": welcome_msg,
            "system_instruction": system_instruction,
            "knowledge_base": knowledge_base_id,
            "agent_id": agent.id,
            "user_id": user,
            "dynamic_variables": dynamic_variables,
            "noise_setting_variables": noise_setting_variables,
            "uid": str(uid),
            "custom_functions_list": custom_functions_list,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "is_custom_voice":voice_obj.is_custom_voice,
            "custom_voice_id":custom_voice_id
        }
        
        await run_bot(websocket, **params)
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", exc_info=True)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)




