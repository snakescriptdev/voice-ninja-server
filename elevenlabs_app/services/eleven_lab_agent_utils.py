import requests
import logging
import json
from time import sleep
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from elevenlabs import ElevenLabs
from elevenlabs_app.elevenlabs_config import DEFAULT_LLM_ELEVENLAB,DEFAULT_MODEL_ELEVENLAB,VALID_LLMS

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

    def get_voice(self, voice_id: str = None,retries: int = 3, **kwargs) -> ElevenLabsAgentResponse:
        """
        Fetch a single voice (if voice_id provided) or list voices with filters.

        Accepted kwargs for listing voices:
            next_page_token: str | None
            page_size: int (max 100, default 10)
            search: str | None
            sort: str | None ('created_at_unix' or 'name')
            sort_direction: str | None ('asc' or 'desc')
            voice_type: str | None ('personal','community','default','workspace','non-default')
            category: str | None ('premade','cloned','generated','professional')
            fine_tuning_state: str | None ('draft','not_verified','not_started','queued','fine_tuning','fine_tuned','failed','delayed')
            collection_id: str | None
            include_total_count: bool (default True)
            voice_ids: list[str] | None (max 100)
        """
        params = {k: v for k, v in kwargs.items() if v is not None}
        error_message = None

        for attempt in range(1, retries + 1):
            try:
                if voice_id:
                    voice = self.client.voices.get(voice_id=voice_id)
                    return ElevenLabsAgentResponse(status=True, data={"voice": voice})
                voices = self.client.voices.list(**params)
                return ElevenLabsAgentResponse(status=True, data={"voices": voices})
            except Exception as e:
                error_message = str(e)
                logger.error(f"Attempt {attempt}: {error_message}")
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
    model_id: str = DEFAULT_MODEL_ELEVENLAB
    voice_id: Optional[str] = None
    agent_output_audio_format: str = "pcm_16000"
    optimize_streaming_latency: int = 3
    stability: float = 0.5
    speed: float = 1.0
    similarity_boost: float = 0.8
    pronunciation_dictionary_locators: List[dict] = field(default_factory=list)

@dataclass
class PromptConfig:
    prompt: str = "You are a helpful assistant"
    llm: str = DEFAULT_LLM_ELEVENLAB
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
        result = asdict(self)
        # Remove None values that ElevenLabs API doesn't accept
        if result.get('tts', {}).get('voice_id') is None:
            del result['tts']['voice_id']
        return result


class ElevenLabsAgentCRUD:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or ELEVEN_API_KEY
        self.headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json"
        }

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Get agent details from ElevenLabs API
        """
        try:
            url = f"{BASE_URL}/convai/agents/{agent_id}"
            resp = requests.get(url, headers=self.headers)
            
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": "Failed to get agent", "exc": f"Status: {resp.status_code}, Response: {resp.text}"}
        except Exception as ex:
            return {"error": "Error occurred", "exc": str(ex)}

    def get_agent_tools(self, agent_id: str) -> Dict[str, Any]:
        """
        Get all tools for an agent from ElevenLabs API
        DEPRECATED: Use get_agent() and extract tools from the response instead
        """
        try:
            url = f"{BASE_URL}/convai/agents/{agent_id}/tools"
            resp = requests.get(url, headers=self.headers)
            
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": "Failed to get agent tools", "exc": f"Status: {resp.status_code}, Response: {resp.text}"}
        except Exception as ex:
            return {"error": "Error occurred", "exc": str(ex)}
    

    def create_agent(
        self,
        name: str,
        prompt: str,
        model: str,
        voice_id: str,
        language: str,
        selected_elevenlab_model: str,
        first_message:str
    ) -> Dict[str, Any]:

        try:
            # Build conversation config with overrides
            config = ConversationConfig()
            config.agent.prompt.prompt = prompt
            config.agent.prompt.llm = model
            config.agent.language = language
            config.tts.voice_id = voice_id
            config.tts.model_id = selected_elevenlab_model
            config.agent.first_message = first_message

            payload = {
                "name": name,
                "conversation_config": config.to_dict()
            }

            resp = requests.post(BASE_URL+"/convai/agents/create", headers=self.headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"Failed to create agent: {resp.status_code} {resp.text}")
            return resp.json()
        except Exception as ex:
            return {"error":"Error Occurred","exc":str(ex)}



    def create_webhook_function(self, webhook_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a webhook function in ElevenLabs.
        """
        try:
            url = f"{BASE_URL}/convai/tools"
            
            # The payload should be: {"tool_config": <config>}
            payload = {"tool_config": webhook_config}
            
            # print(f"Debug: Creating webhook function at: {url}")
            # print(f"Debug: Payload: {json.dumps(payload, indent=2)}")
            
            resp = requests.post(url, headers=self.headers, json=payload)
            
            # print(f"Debug: ElevenLabs response status: {resp.status_code}")
            # print(f"Debug: ElevenLabs response: {resp.text}")
            
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": "Failed to create webhook function", "exc": resp.text}
                
        except Exception as ex:
            return {"error": "Error Occurred", "exc": str(ex)}

    def update_agent_tools(self, agent_id: str, tool_ids: List[str]) -> Dict[str, Any]:
        """
        Update an agent to attach tools by updating the agent's prompt configuration.
        """
        try:
            # print(f"🔍 Debug: Updating agent {agent_id} with tool IDs: {tool_ids}")
            
            # Use the existing update_agent method with tool_ids
            result = self.update_agent(
                agent_id=agent_id,
                tool_ids=tool_ids
            )
            
            # print(f"🔍 Debug: Update agent result: {result}")
            return result
                
        except Exception as ex:
            return {"error": "Error Occurred", "exc": str(ex)}

    def delete_webhook_function(self, tool_id: str) -> Dict[str, Any]:
        """
        Delete a webhook function from ElevenLabs.
        """
        try:
            url = f"{BASE_URL}/convai/tools/{tool_id}"
            # print(f"🔍 Debug: Deleting webhook function at: {url}")
            
            resp = requests.delete(url, headers=self.headers)
            # print(f"🔍 Debug: ElevenLabs response status: {resp.status_code}")
            # print(f"🔍 Debug: ElevenLabs response: {resp.text}")
            
            # 204 No Content is a successful deletion response
            if resp.status_code in [200, 204]:
                return {"success": True, "message": "Tool deleted successfully"}
            else:
                return {"error": "Failed to delete webhook function", "exc": resp.text}
                
        except Exception as ex:
            return {"error": "Error Occurred", "exc": str(ex)}

    def update_webhook_tool(self, tool_id: str, tool_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a webhook tool in ElevenLabs.
        """
        try:
            url = f"{BASE_URL}/convai/tools/{tool_id}"
            # print(f"🔍 Debug: Updating webhook tool at: {url}")
            
            payload = {"tool_config": tool_config}
            resp = requests.patch(url, headers=self.headers, json=payload)
            
            # print(f"🔍 Debug: ElevenLabs response status: {resp.status_code}")
            # print(f"🔍 Debug: ElevenLabs response: {resp.text}")
            
            if resp.status_code == 200:
                return {"success": True, "message": "Tool updated successfully", "data": resp.json()}
            else:
                return {"error": "Failed to update webhook tool", "exc": f"Status: {resp.status_code}, Response: {resp.text}"}
                
        except Exception as ex:
            return {"error": "Error Occurred", "exc": str(ex)}

    def update_agent(
        self,
        agent_id: str,
        name: Optional[str] = None,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        voice_id: Optional[str] = None,
        language: Optional[str] = None,
        selected_elevenlab_model: Optional[str] = None,
        first_message: Optional[str] = None,
        dynamic_variables: Optional[Dict[str, Any]] = None,
        knowledge_base: Optional[List[Dict[str, str]]] = None,
        tool_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Update an existing agent.
        Only non-None parameters will override the current configuration.
        
        Args:
            knowledge_base: List of dicts with format [{"name": "filename.pdf", "id": "elevenlabs_doc_id", "type": "file"}]
        """

        try:
            # First, get the current agent configuration to preserve existing settings
            current_agent = self.get_agent(agent_id)
            if "error" in current_agent:
                raise Exception(f"Failed to get current agent: {current_agent}")
            
            # Start with the current conversation config
            current_config = current_agent.get("conversation_config", {})
            
            # Remove tools array to avoid conflict with tool_ids (ElevenLabs doesn't allow both)
            if "agent" in current_config and "prompt" in current_config["agent"]:
                if "tools" in current_config["agent"]["prompt"]:
                    del current_config["agent"]["prompt"]["tools"]
            
            # Create payload starting with current config
            payload = {}
            if name:
                payload["name"] = name
            
            # Update conversation config only if we have changes
            config_updated = False
            
            # Handle prompt update
            if prompt:
                if "agent" not in current_config:
                    current_config["agent"] = {}
                if "prompt" not in current_config["agent"]:
                    current_config["agent"]["prompt"] = {}
                current_config["agent"]["prompt"]["prompt"] = prompt
                config_updated = True
                # print(f"🔍 Debug: Updated prompt to: {prompt}")
            
            # Handle model update
            if model:
                if "agent" not in current_config:
                    current_config["agent"] = {}
                if "prompt" not in current_config["agent"]:
                    current_config["agent"]["prompt"] = {}
                current_config["agent"]["prompt"]["llm"] = model
                config_updated = True
            
            # Handle language update
            if language:
                if "agent" not in current_config:
                    current_config["agent"] = {}
                current_config["agent"]["language"] = language
                config_updated = True
            
            # Handle voice_id update
            if voice_id:
                if "tts" not in current_config:
                    current_config["tts"] = {}
                current_config["tts"]["voice_id"] = voice_id
                config_updated = True
            
            # Handle model_id update
            if selected_elevenlab_model:
                if "tts" not in current_config:
                    current_config["tts"] = {}
                current_config["tts"]["model_id"] = selected_elevenlab_model
                config_updated = True
            
            # Handle first_message update
            if first_message:
                if "agent" not in current_config:
                    current_config["agent"] = {}
                current_config["agent"]["first_message"] = first_message
                config_updated = True
            
            # Handle knowledge base update
            if knowledge_base:
                if "agent" not in current_config:
                    current_config["agent"] = {}
                if "prompt" not in current_config["agent"]:
                    current_config["agent"]["prompt"] = {}
                current_config["agent"]["prompt"]["knowledge_base"] = knowledge_base
                config_updated = True
                # print(f"🔍 Debug: Updated knowledge base to: {knowledge_base}")
            
            # Handle tool_ids update
            if tool_ids is not None:
                if "agent" not in current_config:
                    current_config["agent"] = {}
                if "prompt" not in current_config["agent"]:
                    current_config["agent"]["prompt"] = {}
                current_config["agent"]["prompt"]["tool_ids"] = tool_ids
                # Remove tools array to avoid conflict (ElevenLabs doesn't allow both)
                if "tools" in current_config["agent"]["prompt"]:
                    del current_config["agent"]["prompt"]["tools"]
                config_updated = True
                # print(f"🔍 Debug: Updated tool_ids to: {tool_ids}")
            
            # Handle dynamic variables update
            if dynamic_variables:
                if "agent" not in current_config:
                    current_config["agent"] = {}
                current_config["agent"]["dynamic_variables"] = {
                    "dynamic_variable_placeholders": dynamic_variables
                }
                config_updated = True
                # print(f"🔍 Debug: Updated dynamic variables to: {dynamic_variables}")
            
            # Add conversation config to payload if any changes were made
            if config_updated:
                payload["conversation_config"] = current_config
            
            # Call the update endpoint
            url = f"{BASE_URL}/convai/agents/{agent_id}"
            # print(f"🔍 Debug: Calling ElevenLabs update endpoint: {url}")
            # print(f"🔍 Debug: Payload: {json.dumps(payload, indent=2)}")
            
            resp = requests.patch(url, headers=self.headers, json=payload)
            # print(f"🔍 Debug: Response status: {resp.status_code}")
            # print(f"🔍 Debug: Response text: {resp.text}")

            if resp.status_code != 200:
                raise Exception(f"Failed to update agent: {resp.status_code} {resp.text}")

            return resp.json()
        except Exception as ex:
            return {"error": "Error Occurred", "exc": str(ex)}


    def delete_agent(self, agent_id: str) -> Dict[str, Any]:
            """
            Delete an existing agent by agent_id
            """
            try:
                url = f"{BASE_URL}/convai/agents/delete"
                payload = {"agent_id": agent_id}

                resp = requests.post(url, headers=self.headers, json=payload)

                if resp.status_code != 200:
                    raise Exception(f"Failed to delete agent: {resp.status_code} {resp.text}")

                return resp.json()
            except Exception as ex:
                return {"error": "Error Occurred", "exc": str(ex)}
            
    def upload_file_to_knowledge_base(self, file_path: str, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Upload a file to ElevenLabs Knowledge Base
        
        Args:
            file_path (str): Path to the file to upload
            name (str, optional): Custom human-readable name for the document
            
        Returns:
            Dict[str, Any]: Response from the API or error information
        """
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                return {"error": "File not found", "exc": f"File {file_path} does not exist"}
            # Get filename if no custom name provided
            if not name:
                name = os.path.basename(file_path)
            # Prepare the multipart form data
            with open(file_path, "rb") as f:
                files = {"file": (name, f, self._get_mime_type(file_path))}
                data = {}
                if name:
                    data["name"] = name
                
                # Use the correct endpoint for knowledge base file upload
                url = f"{BASE_URL}/convai/knowledge-base/file"
                # Headers should not include Content-Type for multipart form data
                headers = {"xi-api-key": ELEVEN_API_KEY}
                response = requests.post(url, headers=headers, files=files, data=data)
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ File uploaded successfully to knowledge base: {name}")
                    return result
                else:
                    error_msg = f"Failed to upload file: {response.status_code} {response.text}"
                    logger.error(error_msg)
                    return {"error": "Upload failed", "exc": error_msg}
                    
        except Exception as ex:
            error_msg = f"Error occurred while uploading file: {str(ex)}"
            logger.error(error_msg)
            return {"error": "Error Occurred", "exc": error_msg}
    
    def _get_mime_type(self, file_path: str) -> str:
        """
        Get the MIME type for a file based on its extension
        """
        import mimetypes
        
        # Get file extension
        _, ext = os.path.splitext(file_path.lower())
        # Map common extensions to ElevenLabs expected MIME types
        mime_map = {
            '.pdf': 'application/pdf',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.txt': 'text/plain',
            '.html': 'text/html',
            '.htm': 'text/html',
            '.epub': 'application/epub+zip'
        }
        
        # Return mapped MIME type or guessed type
        if ext in mime_map:
            mime_type = mime_map[ext]
            return mime_type
        
        # Fallback to mimetypes library
        mime_type, _ = mimetypes.guess_type(file_path)
        return mime_type or 'application/octet-stream'
    
    def delete_file_from_knowledge_base(self, elevenlabs_doc_id: str) -> Dict[str, Any]:
        """
        Delete a file from ElevenLabs Knowledge Base
        
        Args:
            elevenlabs_doc_id (str): The ElevenLabs document ID to delete
            
        Returns:
            Dict[str, Any]: Response from the API or error information
        """
        try:
            # Use the correct DELETE endpoint with document ID in URL path
            url = f"{BASE_URL}/convai/knowledge-base/{elevenlabs_doc_id}"
            headers = {"xi-api-key": ELEVEN_API_KEY}
            response = requests.delete(url, headers=headers)
            if response.status_code in [200, 204]:
                # For DELETE operations, 204 (No Content) is the standard success response
                # 200 is also acceptable but less common
                if response.status_code == 204:
                    logger.info(f"✅ File deleted successfully from knowledge base: {elevenlabs_doc_id} (204 No Content)")
                    return {"status": "success", "message": "File deleted successfully"}
                else:
                    result = response.json()
                    logger.info(f"✅ File deleted successfully from knowledge base: {elevenlabs_doc_id}")
                    return result
            else:
                error_msg = f"Failed to delete file: {response.status_code} {response.text}"
                logger.error(error_msg)
                return {"error": "Delete failed", "exc": error_msg}
                
        except Exception as ex:
            error_msg = f"Error occurred while deleting file: {str(ex)}"
            logger.error(error_msg)
            return {"error": "Error Occurred", "exc": error_msg}
