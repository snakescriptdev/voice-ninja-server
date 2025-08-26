import requests
import logging
from time import sleep
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from elevenlabs import ElevenLabs

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") 
BASE_URL = "https://api.elevenlabs.io/v1"

HEADERS = {
    "xi-api-key": ELEVEN_API_KEY,
    "Content-Type": "application/json"
}

class ElevenLabsAgentResponse:
    def __init__(self, status: bool, data: Optional[Any] = None, error_message: str = ""):
        self.status = status
        self.data = data
        self.error_message = error_message

    def to_dict(self):
        return {
            "status": self.status,
            "data": self.data,
            "error_message": self.error_message
        }


class ElevenLabsBase:
    def __init__(self):
        self.client = ElevenLabs(api_key=ELEVEN_API_KEY)
        self.api_key = ELEVEN_API_KEY
        self.base_url = BASE_URL
        self.headers = {"xi-api-key": self.api_key}


class ElevenLabsVoice(ElevenLabsBase):
    def __init__(self):
        super().__init__()

    def create_cloned_voice(self, file_path: str, name: str = "MyCustomVoice", retries: int = 3) -> ElevenLabsAgentResponse:
        """
        Add a custom voice by uploading an audio file via ElevenLabs API.
        """
        for attempt in range(1, retries + 1):
            try:
                with open(file_path, "rb") as f:
                    files = {"files": f}
                    data = {"name": name}
                    response = requests.post(f"{self.base_url}/voices/add", headers=self.headers, files=files, data=data)

                if response.status_code == 200:
                    result = response.json()
                    voice_id = result.get("voice_id")
                    logger.info(f"✅ Voice cloned successfully! voice_id = {voice_id}")
                    return ElevenLabsAgentResponse(status=True, data={"voice_id": voice_id})
                else:
                    error_message = f"Attempt {attempt}: Failed to clone voice. Status {response.status_code}, Response: {response.text}"
                    logger.warning(error_message)
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Exception while creating cloned voice: {error_message}")

            sleep(2)

        return ElevenLabsAgentResponse(status=False, error_message=error_message)

    def get_voice(self, voice_id: str, retries: int = 3) -> ElevenLabsAgentResponse:
        for attempt in range(1, retries + 1):
            try:
                voice = self.client.voices.get(voice_id=voice_id)
                logger.info(f"✅ Retrieved voice {voice_id}")
                return ElevenLabsAgentResponse(status=True, data={"voice": voice})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsAgentResponse(status=False, error_message=error_message)

    def edit_voice_name(self, voice_id: str, new_name: str, retries: int = 3) -> ElevenLabsAgentResponse:
        check = self.get_voice(voice_id)
        if not check.status:
            return ElevenLabsAgentResponse(status=False, error_message=f"Voice not found: {voice_id}")

        for attempt in range(1, retries + 1):
            try:
                updated_voice = self.client.voices.update(voice_id=voice_id, name=new_name)
                logger.info(f"✅ Voice {voice_id} renamed successfully to {new_name}")
                return ElevenLabsAgentResponse(status=True, data={"voice_id": voice_id, "name":new_name})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsAgentResponse(status=False, error_message=f"Failed to edit voice name after {retries} attempts")

    def delete_voice(self, voice_id: str, retries: int = 3) -> ElevenLabsAgentResponse:
        check = self.get_voice(voice_id)
        if not check.status:
            return ElevenLabsAgentResponse(status=False, error_message=f"Voice not found: {voice_id}")

        for attempt in range(1, retries + 1):
            try:
                self.client.voices.delete(voice_id=voice_id)
                logger.info(f"✅ Voice {voice_id} deleted successfully")
                return ElevenLabsAgentResponse(status=True, data={"voice_id": voice_id})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsAgentResponse(status=False, error_message=f"Failed to delete voice after {retries} attempts")

    def get_all_voices(self, retries: int = 3) -> ElevenLabsAgentResponse:
        for attempt in range(1, retries + 1):
            try:
                voices = self.client.voices.search(include_total_count=True)
                logger.info(f"✅ Retrieved {len(voices.voices)} voices")
                return ElevenLabsAgentResponse(status=True, data={"voices": voices.voices})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: Unexpected error: {error_message}")
            sleep(2)
        return ElevenLabsAgentResponse(status=False, error_message=error_message)

class ElevenLabsAgentUtils:
    def __init__(self):
        super().__init__()

    def construct_api_schema(self,kwargs):
        url = kwargs.get("url")
        method = kwargs.get("method")
        path_params_schema = kwargs.get("path_params_schema")
        query_params_schema = kwargs.get("query_params_schema")
        request_body_schema = kwargs.get("request_body_schema")
        request_headers = kwargs.get("request_headers")
        auth_connection = kwargs.get("auth_connection")


    def create_tool(self,kwargs):
        name = kwargs.get("name")
        description = kwargs.get("description")
        api_schema = kwargs.get("api_schema")
        type = kwargs.get("type")
        response_timeout_secs = kwargs.get("response_timeout_secs")
        disable_interruptions = kwargs.get("disable_interruptions")
        force_pre_tool_speech = kwargs.get("force_pre_tool_speech")
        assignments = kwargs.get("assignments")
        dynamic_variables = kwargs.get("dynamic_variables")
        
        
@dataclass
class AsrConfig:
    provider: str = "elevenlabs"
    quality: str = "high"
    user_input_audio_format: str = "pcm_16000"
    keywords: List[str] = field(default_factory=list)

@dataclass
class TurnConfig:
    mode: str = "silence"
    turn_timeout: float = 7.0
    silence_end_call_timeout: float = -1.0


@dataclass
class TtsConfig:
    model_id: str = "eleven_turbo_v2"
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    agent_output_audio_format: str = "pcm_16000"
    optimize_streaming_latency: int = 3
    stability: float = 0.5
    speed: float = 1.0
    similarity_boost: float = 0.8
    pronunciation_dictionary_locators: List[dict] = field(default_factory=list)

@dataclass
class PromptConfig:
    prompt: str = "You are a helpful assistant"
    llm: str = "gemini-2.0-flash"
    temperature: float = 0.0
    max_tokens: int = -1
    tool_ids: List[str] = field(default_factory=list)
    mcp_server_ids: List[str] = field(default_factory=list)
    native_mcp_server_ids: List[str] = field(default_factory=list)
    knowledge_base: List[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    first_message: str = "Hello! I'm your AI assistant. How can I help?"
    language: str = "en"
    prompt: PromptConfig = field(default_factory=PromptConfig)
    tags: List[str] = field(default_factory=list)

@dataclass
class ConversationConfig:
    asr: AsrConfig = field(default_factory=AsrConfig)
    turn: TurnConfig = field(default_factory=TurnConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    def to_dict(self) -> dict:
        return asdict(self)


class ElevenLabsAgentCreator:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or ELEVEN_API_KEY
        self.headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json"
        }

    def create_agent(
        self,
        name: str,
        prompt: str,
        model: str,
        voice_id: str,
        language: str
    ) -> Dict[str, Any]:
        # Build conversation config with overrides
        config = ConversationConfig()
        config.agent.prompt.prompt = prompt
        config.agent.prompt.llm = model
        config.agent.language = language
        config.tts.voice_id = voice_id

        payload = {
            "name": name,
            "conversation_config": config.to_dict()
        }

        resp = requests.post(BASE_URL+"/convai/agents/create", headers=self.headers, json=payload)
        if resp.status_code != 200:
            raise Exception(f"Failed to create agent: {resp.status_code} {resp.text}")
        return resp.json()
