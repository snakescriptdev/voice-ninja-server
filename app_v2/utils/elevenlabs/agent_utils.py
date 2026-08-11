"""
ElevenLabs Agent Utilities

This module provides utilities for agent-related operations with the ElevenLabs API.
Handles agent creation, retrieval, updating, deletion, and configuration management.
"""

import json
import re
from typing import Optional, Dict, Any, List
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger
from app_v2.core.elevenlabs_config import (
    DEFAULT_LLM_ELEVENLAB,
    DEFAULT_MODEL_ELEVENLAB,
    DEFAULT_LANGUAGE,
    get_compatible_model_for_language
)
from app_v2.schemas.function_schema import ApiSchema

logger = setup_logger(__name__)

# Known ElevenLabs create/update-agent failure statuses mapped to a short,
# actionable message a user can actually do something with. Anything not in
# this map falls back to the caller's own generic message — the raw
# ElevenLabs body (request_id, "knowledge base size" wording) is for logs
# only, never for users: this API doesn't feed anything into ElevenLabs'
# native knowledge_base field (personal KB is a search tool, not that field),
# so a "knowledge base too large" error is really the prompt/model context
# budget being exceeded and should read that way to the caller.
_FRIENDLY_AGENT_SYNC_ERRORS = {
    "file_too_large": (
        "The prompt length is too long for this model. Please try changing "
        "the model or reducing the prompt size."
    ),
    "rag_documents_size_too_large": (
        "The agent's selected model and prompt is too large. Please decrease "
        "or remove some prompt content."
    ),
}


def describe_agent_sync_error(raw_error_message: Optional[str]) -> Optional[str]:
    """
    Translate a raw ElevenLabs create/update-agent error (typically
    `Status <code>: <json body>`) into a short, user-facing message for the
    known failure statuses in _FRIENDLY_AGENT_SYNC_ERRORS. Returns None for
    anything else so the caller falls back to its own generic message.
    """
    match = re.search(r"\{.*\}", raw_error_message or "", re.DOTALL)
    if not match:
        return None
    try:
        body = json.loads(match.group(0))
        detail = body.get("detail") if isinstance(body, dict) else None
        error_status = detail.get("status") if isinstance(detail, dict) else None
    except (ValueError, AttributeError, TypeError):
        return None
    return _FRIENDLY_AGENT_SYNC_ERRORS.get(error_status)


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
        tts_model: Optional[str] = None,
        tool_ids: Optional[List[str]] = None,
        knowledge_base: Optional[List[Dict[str, str]]] = None,
        dynamic_variables: Optional[Dict[str, Any]] = None,
        built_in_tools: Optional[Dict[str, Any]] = None,
        timezone: Optional[str] = None
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
            tool_ids: Optional list of tool IDs to attach
            knowledge_base: Optional list of KB documents
            dynamic_variables: Optional dict of dynamic variable placeholders
            built_in_tools: Optional dict of built-in tools configuration
            timezone: Optional IANA timezone (must be valid for tzinfo), sent to
                ElevenLabs as conversation_config.agent.prompt.timezone

        Returns:
            ElevenLabsResponse with agent_id on success
        """
        logger.info(f"Creating agent: {name} with voice {voice_id}")

        # Build conversation config
        conversation_config = {
            "agent": {
                "prompt": {
                    "prompt": prompt,
                    "llm": llm_model,
                    "temperature": 0.0,
                    "max_tokens": -1,
                    "tool_ids": tool_ids or [],
                    "knowledge_base": knowledge_base or [],
                    # RAG is only meaningful (and only budget-checked by
                    # ElevenLabs against the model's context window) when
                    # there's actual knowledge_base content to search.
                    # Forcing it on unconditionally used to make ElevenLabs
                    # reject the request outright for small-context models
                    # (e.g. gpt-4-0613, 8k tokens) even with an empty
                    # knowledge_base and a short prompt — see
                    # rag_documents_size_too_large in describe_agent_sync_error.
                    "rag": {
                        "enabled": bool(knowledge_base)
                    }
                },
                "first_message": first_message,
                "language": language
            },
            "tts": {
                "model_id": tts_model or get_compatible_model_for_language(language),
                "voice_id": voice_id,
                "agent_output_audio_format": "pcm_16000",
                "optimize_streaming_latency": 3,
                "stability": 0.5,
                "speed": 1.0,
                "similarity_boost": 0.8,
                "text_normalisation_type": "elevenlabs"
            },
            "asr": {
                "provider": "scribe_realtime",
                "quality": "high",
                "user_input_audio_format": "pcm_16000",
                "keywords": []
            },
            "turn": {
                "turn_timeout": 1.0,
                "silence_end_call_timeout": 60,
                "turn_eagerness": "eager"
            }
        }

        if timezone:
            conversation_config["agent"]["prompt"]["timezone"] = timezone

        if dynamic_variables:
            conversation_config["agent"]["dynamic_variables"] = {
                "dynamic_variable_placeholders": dict(dynamic_variables)
            }

        if built_in_tools:
            conversation_config["agent"]["prompt"]["built_in_tools"] = built_in_tools
        
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

    def calculate_llm_usage(self, agent_id: str) -> ElevenLabsResponse:
        """
        Fetch ElevenLabs' expected LLM usage for an agent.

        POSTs an empty body to /convai/agent/{agent_id}/llm-usage/calculate so
        ElevenLabs derives prompt_length / number_of_pages / rag_enabled from the
        agent's own stored configuration. The response is:
            {"llm_prices": [{"llm": "gpt-4o", "price_per_minute": 0.046...}, ...]}

        NOTE: this is a STATIC, pre-call estimate (a floor). It is computed from
        the agent's config only and does NOT account for tool calls, tool
        results, or RAG runtime — actual per-minute LLM cost can be several times
        higher. Use it for the live low-balance cutoff, never for final billing
        (which reconciles against the real reported credits after a call ends).

        Args:
            agent_id: ElevenLabs agent ID.

        Returns:
            ElevenLabsResponse whose data contains the "llm_prices" list.
        """
        logger.info(f"Calculating expected LLM usage for agent: {agent_id}")
        response = self._post(f"/convai/agent/{agent_id}/llm-usage/calculate", data={})

        if response.status:
            logger.info(f"✅ LLM usage calculated for agent {agent_id}")
        else:
            logger.error(f"Failed to calculate LLM usage for {agent_id}: {response.error_message}")

        return response

    @staticmethod
    def extract_price_for_model(llm_usage_data: Dict[str, Any], model_name: str) -> Optional[float]:
        """
        Pull the price_per_minute (USD) for a specific model out of a
        calculate_llm_usage() response.

        Matches the model exactly first, then falls back to a normalized
        (case-insensitive) match so minor id variations still resolve. Returns
        None if the model isn't present in the price list.
        """
        prices = (llm_usage_data or {}).get("llm_prices") or []
        if not model_name:
            return None

        for entry in prices:
            if entry.get("llm") == model_name:
                return entry.get("price_per_minute")

        target = model_name.strip().lower()
        for entry in prices:
            if str(entry.get("llm", "")).strip().lower() == target:
                return entry.get("price_per_minute")

        logger.warning(f"Model '{model_name}' not found in llm_prices list")
        return None

    def get_llm_price_per_minute(self, agent_id: str, model_name: str) -> Optional[float]:
        """
        Convenience: fetch the agent's expected LLM usage and return the
        price_per_minute (USD) for `model_name`. Returns None on any failure so
        callers can store the price best-effort without breaking agent
        create/update if ElevenLabs is unavailable.
        """
        try:
            resp = self.calculate_llm_usage(agent_id)
            if not resp.status or not resp.data:
                return None
            return self.extract_price_for_model(resp.data, model_name)
        except Exception:
            logger.exception(f"Failed to resolve LLM price for agent {agent_id}, model {model_name}")
            return None
    
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
        dynamic_variables: Optional[Dict[str, Any]] = None,
        built_in_tools: Optional[Dict[str, Any]] = None,
        timezone: Optional[str] = None
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
            built_in_tools: Built-in tools configuration
            timezone: IANA timezone (must be valid for tzinfo), sent to ElevenLabs
                as conversation_config.agent.prompt.timezone

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
            # Keep rag.enabled in sync with whether there's still any
            # knowledge_base content — otherwise removing the last document
            # leaves RAG stuck enabled with nothing to search, which can
            # make ElevenLabs reject a later model change on this same
            # agent (small-context models fail the RAG budget check even
            # against an empty knowledge_base once rag.enabled is true).
            current_config["agent"]["prompt"]["rag"] = {"enabled": bool(knowledge_base)}
            config_updated = True
        
        if dynamic_variables is not None:
            if "agent" not in current_config:
                current_config["agent"] = {}
            if "dynamic_variables" not in current_config["agent"]:
                 current_config["agent"]["dynamic_variables"] = {}

            current_config["agent"]["dynamic_variables"]["dynamic_variable_placeholders"] = {
                 key: value for key, value in dynamic_variables.items()
            } if dynamic_variables else {}
            config_updated = True

        if timezone is not None:
            if "agent" not in current_config:
                current_config["agent"] = {}
            if "prompt" not in current_config["agent"]:
                current_config["agent"]["prompt"] = {}
            current_config["agent"]["prompt"]["timezone"] = timezone
            config_updated = True

        if built_in_tools is not None:
            if "agent" not in current_config:
                current_config["agent"] = {}
            if "prompt" not in current_config["agent"]:
                current_config["agent"]["prompt"] = {}
            current_config["agent"]["prompt"]["built_in_tools"] = built_in_tools
            config_updated = True
        
        if config_updated:
            # Always ensure exclusivity of tools and tool_ids in the prompt config
            # ElevenLabs rejects requests that contain both fields
            if "agent" in current_config and "prompt" in current_config["agent"]:
                prompt_config = current_config["agent"]["prompt"]
                if "tool_ids" in prompt_config and "tools" in prompt_config:
                    logger.info("Removing 'tools' from retrieved config to avoid conflict with 'tool_ids'")
                    del prompt_config["tools"]
            
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
        """
        logger.info(f"Fetching tools for agent: {agent_id}")
        
        agent_response = self.get_agent(agent_id)
        if not agent_response.status:
            return agent_response
        
        conversation_config = agent_response.data.get("conversation_config", {})
        tool_ids = conversation_config.get("agent", {}).get("prompt", {}).get("tool_ids", [])
        
        logger.info(f"✅ Agent {agent_id} has {len(tool_ids)} tools")
        return ElevenLabsResponse(status=True, data={"tool_ids": tool_ids})

    def get_tool(self, tool_id: str) -> ElevenLabsResponse:
        """
        Get tool details by tool_id.
        """
        logger.info(f"Fetching ElevenLabs tool: {tool_id}")
        response = self._get(f"/convai/tools/{tool_id}")
        if response.status:
            logger.info(f"✅ Tool fetched: {tool_id}")
        else:
            logger.error(f"Failed to fetch tool: {response.error_message}")
        return response

    def _build_tool_payload(
        self,
        name: str,
        description: str,
        api_schema: ApiSchema,
        pre_tool_speech: Optional[str] = None,
    ) -> dict:
        """
        Build a robust flat payload for ElevenLabs webhook tools.
        The 'webhook' in error locs refers to the model type, not a nested key.
        Handles removal of empty property dicts to satisfy EL validator.

        `pre_tool_speech` controls whether the agent narrates before calling
        this tool (e.g. "let me check that for you") — pass "off" to suppress
        it for tools whose result should just be relayed silently. Left unset
        (EL default "auto") for regular user-created tools, where that filler
        speech is often wanted while a slower action runs.
        """

        # Serialize api_schema
        serialized_api = {
            "url": api_schema.url,
            "method": api_schema.method.value if hasattr(api_schema.method, 'value') else api_schema.method,
            "request_headers": api_schema.request_headers or {},
            "content_type": api_schema.content_type.value if hasattr(api_schema.content_type, 'value') else (api_schema.content_type or "application/json"),
        }

        # Omit empty schemas because EL validator rejects empty properties
        # Path params schema
        if api_schema.path_params_schema:
            serialized_api["path_params_schema"] = {
                k: v.model_dump() if hasattr(v, "model_dump") else v
                for k, v in api_schema.path_params_schema.items()
            }

        # Query params schema - check properties
        if api_schema.query_params_schema:
            qp_dump = api_schema.query_params_schema.model_dump(exclude_none=True)
            if qp_dump.get("properties"):
                serialized_api["query_params_schema"] = qp_dump

        # Request body schema - check properties
        if api_schema.request_body_schema:
            rb_dump = api_schema.request_body_schema.model_dump(exclude_none=True)
            if rb_dump.get("properties"):
                 serialized_api["request_body_schema"] = rb_dump

        tool_config = {
            "type": "webhook",
            "name": name,
            "description": description,
            "api_schema": serialized_api
        }
        if pre_tool_speech is not None:
            tool_config["pre_tool_speech"] = pre_tool_speech

        return {"tool_config": tool_config}

    def create_tool(
        self,
        name: str,
        description: str,
        api_schema: ApiSchema,
        pre_tool_speech: Optional[str] = None,
    ) -> ElevenLabsResponse:
        """
        Create a webhook tool in ElevenLabs ConvAI.
        """
        logger.info(f"Creating ElevenLabs tool: {name}")

        payload = self._build_tool_payload(name, description, api_schema, pre_tool_speech=pre_tool_speech)
        response = self._post("/convai/tools", data=payload)

        if response.status:
            tool_id = response.data.get("id")
            logger.info(f"✅ Tool created successfully: {tool_id}")
        else:
            logger.error(f"❌ Tool creation failed: {response.error_message}")

        return response


    def delete_tool(self, tool_id: str) -> ElevenLabsResponse:
        """
        Delete a tool from ElevenLabs ConvAI.

        Args:
            tool_id: ElevenLabs tool ID

        Returns:
            ElevenLabsResponse
        """

        logger.info(f"Deleting ElevenLabs tool: {tool_id}")

        response = self._delete(f"/convai/tools/{tool_id}")

        if response.status:
            logger.info(f"✅ Tool deleted successfully: {tool_id}")
        else:
            logger.error(f"❌ Failed to delete tool: {response.error_message}")

        return response

    def update_tool(
        self,
        tool_id: str,
        name: str,
        description: str,
        api_schema: ApiSchema,
    ) -> ElevenLabsResponse:
        """
        Update a tool in ElevenLabs ConvAI using PATCH.
        Expects mandatory fields to reconstruct a clean nested payload.
        """
        logger.info(f"Updating ElevenLabs tool: {tool_id}")

        payload = self._build_tool_payload(name, description, api_schema)
        response = self._patch(f"/convai/tools/{tool_id}", data=payload)

        if response.status:
            logger.info(f"✅ Tool updated successfully: {tool_id}")
        else:
            logger.error(f"❌ Failed to update tool: {response.error_message}")

        return response
