"""
ElevenLabs Agent Utilities

This module provides utilities for agent-related operations with the ElevenLabs API.
Handles agent creation, retrieval, updating, deletion, and configuration management.
"""

from typing import Optional, Dict, Any, List
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger
from app_v2.core.elevenlabs_config import (
    DEFAULT_LLM_ELEVENLAB,
    DEFAULT_MODEL_ELEVENLAB,
    DEFAULT_LANGUAGE,
    get_compatible_model_for_language
)

logger = setup_logger(__name__)


class ElevenLabsAgent(BaseElevenLabs):
    """
    Agent utility class for ElevenLabs API operations.
    Handles all agent-related API calls including creation, updates, and configuration.
    """
    
    def create_agent(
        self,
        name: str,
        voice_id: str,
        prompt: str,
        first_message: str = "Hello! How can I help you?",
        language: str = DEFAULT_LANGUAGE,
        llm_model: str = DEFAULT_LLM_ELEVENLAB,
        tts_model: Optional[str] = None
    ) -> ElevenLabsResponse:
        """
        Create a new conversational AI agent in ElevenLabs.
        
        Args:
            name: Agent name
            voice_id: ElevenLabs voice ID to use
            prompt: System prompt for the agent
            first_message: Initial greeting message
            language: Language code (e.g., 'en', 'es')
            llm_model: LLM model to use
            tts_model: TTS model (auto-selected if None)
            
        Returns:
            ElevenLabsResponse with agent_id on success
        """
        logger.info(f"Creating agent: {name} with voice {voice_id}")
        
        # Auto-select compatible TTS model if not provided
        if not tts_model:
            tts_model = get_compatible_model_for_language(language)
            logger.debug(f"Auto-selected TTS model: {tts_model} for language: {language}")
        
        # Build conversation config
        conversation_config = {
            "agent": {
                "prompt": {
                    "prompt": prompt,
                    "llm": llm_model,
                    "temperature": 0.0,
                    "max_tokens": -1,
                    "tool_ids": [],
                    "knowledge_base": [],
                    "rag":{
                        "enabled": True
                    }
                },
                "first_message": first_message,
                "language": language
            },
            "tts": {
                "model_id": tts_model,
                "voice_id": voice_id,
                "agent_output_audio_format": "pcm_16000",
                "optimize_streaming_latency": 3,
                "stability": 0.5,
                "speed": 1.0,
                "similarity_boost": 0.8
            },
            "asr": {
                "provider": "elevenlabs",
                "quality": "high",
                "user_input_audio_format": "pcm_16000",
                "keywords": []
            },
            "turn": {
                "turn_timeout": 7.0,
                "silence_end_call_timeout": -1.0,
                "turn_eagerness": "normal"
            }
        }
        
        payload = {
            "name": name,
            "conversation_config": conversation_config
        }
        
        response = self._post("/convai/agents/create", data=payload)
        
        if response.status:
            agent_id = response.data.get("agent_id")
            logger.info(f"✅ Agent created: {name} (ID: {agent_id})")
        else:
            logger.error(f"Failed to create agent: {response.error_message}")
        
        return response
    
    def get_agent(self, agent_id: str) -> ElevenLabsResponse:
        """
        Get agent details by agent_id.
        
        Args:
            agent_id: ElevenLabs agent ID
            
        Returns:
            ElevenLabsResponse with agent details
        """
        logger.info(f"Fetching agent: {agent_id}")
        response = self._get(f"/convai/agents/{agent_id}")
        
        if response.status:
            logger.info(f"✅ Agent fetched: {agent_id}")
        else:
            logger.error(f"Failed to fetch agent: {response.error_message}")
        
        return response
    
    def update_agent(
        self,
        agent_id: str,
        name: Optional[str] = None,
        voice_id: Optional[str] = None,
        prompt: Optional[str] = None,
        first_message: Optional[str] = None,
        language: Optional[str] = None,
        llm_model: Optional[str] = None,
        tts_model: Optional[str] = None,
        tool_ids: Optional[List[str]] = None,
        knowledge_base: Optional[List[Dict[str, str]]] = None,
        dynamic_variables: Optional[Dict[str, Any]] = None
    ) -> ElevenLabsResponse:
        """
        Update an existing agent.
        Only non-None parameters will override the current configuration.
        
        Args:
            agent_id: ElevenLabs agent ID
            name: New agent name
            voice_id: New voice ID
            prompt: New system prompt
            first_message: New first message
            language: New language code
            llm_model: New LLM model
            tts_model: New TTS model
            tool_ids: List of tool IDs to attach
            knowledge_base: List of KB documents [{\"id\": \"...\", \"type\": \"file\", \"name\": \"...\"}]
            dynamic_variables: Dynamic variables for the agent
            
        Returns:
            ElevenLabsResponse with updated agent data
        """
        logger.info(f"Updating agent: {agent_id}")
        
        # First, get the current agent configuration
        current = self.get_agent(agent_id)
        if not current.status:
            return ElevenLabsResponse(status=False, error_message=f"Agent not found: {agent_id}")
        
        current_config = current.data.get("conversation_config", {})
        
        # Build update payload
        payload = {}
        
        if name:
            payload["name"] = name
        
        # Update conversation config if any changes
        config_updated = False
        
        if prompt:
            if "agent" not in current_config:
                current_config["agent"] = {}
            if "prompt" not in current_config["agent"]:
                current_config["agent"]["prompt"] = {}
            current_config["agent"]["prompt"]["prompt"] = prompt
            config_updated = True
        
        if llm_model:
            if "agent" not in current_config:
                current_config["agent"] = {}
            if "prompt" not in current_config["agent"]:
                current_config["agent"]["prompt"] = {}
            current_config["agent"]["prompt"]["llm"] = llm_model
            config_updated = True
        
        if language:
            if "agent" not in current_config:
                current_config["agent"] = {}
            current_config["agent"]["language"] = language
            config_updated = True
            
            # Auto-adjust TTS model if needed
            if tts_model is None:
                new_tts_model = get_compatible_model_for_language(language)
                if "tts" not in current_config:
                    current_config["tts"] = {}
                current_config["tts"]["model_id"] = new_tts_model
        
        if voice_id:
            if "tts" not in current_config:
                current_config["tts"] = {}
            current_config["tts"]["voice_id"] = voice_id
            config_updated = True
        
        if tts_model:
            if "tts" not in current_config:
                current_config["tts"] = {}
            current_config["tts"]["model_id"] = tts_model
            config_updated = True
        
        if first_message:
            if "agent" not in current_config:
                current_config["agent"] = {}
            current_config["agent"]["first_message"] = first_message
            config_updated = True
        
        if tool_ids is not None:
            if "agent" not in current_config:
                current_config["agent"] = {}
            if "prompt" not in current_config["agent"]:
                current_config["agent"]["prompt"] = {}
            current_config["agent"]["prompt"]["tool_ids"] = tool_ids
            # Remove tools array to avoid conflicts
            if "tools" in current_config["agent"]["prompt"]:
                del current_config["agent"]["prompt"]["tools"]
            config_updated = True
        
        if knowledge_base is not None:
            if "agent" not in current_config:
                current_config["agent"] = {}
            if "prompt" not in current_config["agent"]:
                current_config["agent"]["prompt"] = {}
            current_config["agent"]["prompt"]["knowledge_base"] = knowledge_base
            config_updated = True
        
        if dynamic_variables:
            if "agent" not in current_config:
                current_config["agent"] = {}
            current_config["agent"]["dynamic_variables"]["dynamic_variable_placeholders"]= {
                 key:value for key, value in dynamic_variables.items()
            } if dynamic_variables else None
            config_updated = True
        
        if config_updated:
            payload["conversation_config"] = current_config
        
        if not payload:
            return ElevenLabsResponse(status=False, error_message="No update data provided")
        
        response = self._patch(f"/convai/agents/{agent_id}", data=payload)
        
        if response.status:
            logger.info(f"✅ Agent updated: {agent_id}")
        else:
            logger.error(f"Failed to update agent: {response.error_message}")
        
        return response
    
    def delete_agent(self, agent_id: str) -> ElevenLabsResponse:
        """
        Delete an agent from ElevenLabs.
        
        Args:
            agent_id: ElevenLabs agent ID to delete
            
        Returns:
            ElevenLabsResponse indicating success or failure
        """
        logger.info(f"Deleting agent: {agent_id}")
        response = self._delete(f"/convai/agents/{agent_id}")
        
        if response.status:
            logger.info(f"✅ Agent deleted: {agent_id}")
        else:
            logger.error(f"Failed to delete agent: {response.error_message}")
        
        return response
    
    def get_agent_tools(self, agent_id: str) -> ElevenLabsResponse:
        """
        Get all tools attached to an agent.
        
        Args:
            agent_id: ElevenLabs agent ID
            
        Returns:
            ElevenLabsResponse with list of tools
        """
        logger.info(f"Fetching tools for agent: {agent_id}")
        
        # Get agent and extract tools from conversation config
        agent_response = self.get_agent(agent_id)
        if not agent_response.status:
            return agent_response
        
        conversation_config = agent_response.data.get("conversation_config", {})
        tool_ids = conversation_config.get("agent", {}).get("prompt", {}).get("tool_ids", [])
        
        logger.info(f"✅ Agent {agent_id} has {len(tool_ids)} tools")
        return ElevenLabsResponse(status=True, data={"tool_ids": tool_ids})

    def create_tool(self, tool_config: Dict[str, Any]) -> ElevenLabsResponse:
        """
        Create a new tool in ElevenLabs.
        
        Args:
            tool_config: Dictionary containing tool configuration.
                         Must include 'name', 'type', and type-specific config.
                         
        Returns:
            ElevenLabsResponse with tool_id
        """
        name = tool_config.get("name", "Unnamed Tool")
        logger.info(f"Creating tool: {name}")
        
        response = self._post("/convai/tools", data=tool_config)
        
        if response.status:
            tool_id = response.data.get("id")
            logger.info(f"✅ Tool created: {name} (ID: {tool_id})")
        else:
            logger.error(f"Failed to create tool: {response.error_message}")
            
        return response

    def delete_tool(self, tool_id: str) -> ElevenLabsResponse:
        """
        Delete a tool from ElevenLabs.
        
        Args:
            tool_id: ElevenLabs tool ID
            
        Returns:
            ElevenLabsResponse
        """
        logger.info(f"Deleting tool: {tool_id}")
        
        response = self._delete(f"/convai/tools/{tool_id}")
        
        if response.status:
            logger.info(f"✅ Tool deleted: {tool_id}")
        else:
            logger.error(f"Failed to delete tool: {response.error_message}")
            
        return response
