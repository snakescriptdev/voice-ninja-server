"""
Auto-manages the system "search_personal_knowledge_base" tool: created once a
user has at least one personal-knowledge-base item, attached to every one of
their agents (existing and future), and torn down once their personal
knowledge base is empty again.

Users cannot edit, detach, or delete this tool directly — see the
is_system_managed guards in app_v2/routers/functions.py and
app_v2/routers/agents.py.
"""

import re
from fastapi_sqlalchemy import db

from app_v2.databases.models import (
    FunctionModel,
    FunctionApiConfig,
    AgentModel,
    AgentFunctionBridgeModel,
    PersonalKnowledgeBaseModel,
)
from app_v2.schemas.function_schema import ApiSchema, HttpMethod, ContentType, RequestBodySchema, BodyField
from app_v2.schemas.enum_types import RequestMethodEnum
from app_v2.utils.elevenlabs import ElevenLabsAgent
from app_v2.utils.crypto_utils import encrypt_data
from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

TOOL_NAME = "search_personal_knowledge_base"
TOOL_DESCRIPTION = (
    "Searches the user's personal knowledge base (their uploaded files, URLs, "
    "and notes) for content relevant to a question. Call this tool with the "
    "user's question or topic whenever they ask something that might be "
    "answered by their own uploaded documents or notes."
)

_PROMPT_BLOCK_START = "<!-- personal_kb_tool:start -->"
_PROMPT_BLOCK_END = "<!-- personal_kb_tool:end -->"
_PROMPT_BLOCK_PATTERN = re.compile(
    re.escape(_PROMPT_BLOCK_START) + r".*?" + re.escape(_PROMPT_BLOCK_END), re.DOTALL
)
_PROMPT_BLOCK_TEXT = (
    f"\n\n{_PROMPT_BLOCK_START}\n"
    f"You have access to a tool named `{TOOL_NAME}`. Use {TOOL_NAME} to fetch "
    f"details from the user's personal knowledge base for any user-related "
    f"query that might be answered by their uploaded files, URLs, or notes.\n"
    f"{_PROMPT_BLOCK_END}"
)


def _webhook_base_url() -> str:
    base = VoiceSettings.NGROK_BASE_URL or ""
    if base.startswith("wss://"):
        return base.replace("wss://", "https://")
    if base.startswith("ws://"):
        return base.replace("ws://", "http://")
    return base


def _remove_prompt_block(system_prompt: str) -> str:
    if not system_prompt:
        return system_prompt or ""
    return _PROMPT_BLOCK_PATTERN.sub("", system_prompt).rstrip()


def _add_prompt_block(system_prompt: str) -> str:
    return _remove_prompt_block(system_prompt) + _PROMPT_BLOCK_TEXT


def _get_system_tool(user_id: int) -> FunctionModel:
    return db.session.query(FunctionModel).filter(
        FunctionModel.user_id == user_id,
        FunctionModel.is_system_managed.is_(True),
        FunctionModel.name == TOOL_NAME,
    ).first()


def _create_system_tool(user_id: int) -> FunctionModel:
    secret = VoiceSettings.PERSONAL_KB_TOOL_SECRET
    if not secret:
        logger.warning("PERSONAL_KB_TOOL_SECRET is not set — the personal KB tool webhook will be unauthenticated.")

    url = f"{_webhook_base_url()}/api/v2/personal-knowledge-base/tool-search/{user_id}"
    body_field = {"query": {"type": "string", "description": "The user's question or topic to search for"}}

    api_schema = ApiSchema(
        url=url,
        method=HttpMethod.POST,
        request_headers={"X-Api-Key": secret},
        content_type=ContentType.JSON,
        request_body_schema=RequestBodySchema(
            type="object",
            properties={"query": BodyField(type="string", description="The user's question or topic to search for")},
            required=["query"],
        ),
    )

    el_client = ElevenLabsAgent()
    el_response = el_client.create_tool(name=TOOL_NAME, description=TOOL_DESCRIPTION, api_schema=api_schema)
    if not el_response.status:
        raise RuntimeError(f"Failed to create personal KB tool in ElevenLabs: {el_response.error_message}")
    elevenlabs_tool_id = el_response.data.get("id")

    function = FunctionModel(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        user_id=user_id,
        elevenlabs_tool_id=elevenlabs_tool_id,
        is_system_managed=True,
    )
    db.session.add(function)
    db.session.flush()

    db.session.add(FunctionApiConfig(
        function_id=function.id,
        endpoint_url=url,
        http_method=RequestMethodEnum.post,
        headers={"X-Api-Key": encrypt_data(secret)} if secret else {},
        body_schema={"type": "object", "properties": body_field, "required": ["query"]},
        timeout_ms=30000,
        speak_while_execution=False,
        speak_after_execution=True,
    ))
    db.session.flush()

    logger.info(f"Created personal KB tool for user {user_id}: elevenlabs_tool_id={elevenlabs_tool_id}")
    return function


def _resync_agent(agent: AgentModel, el_client: ElevenLabsAgent) -> None:
    if not agent.elevenlabs_agent_id:
        return
    bound_function_ids = [
        row.function_id for row in db.session.query(AgentFunctionBridgeModel)
        .filter(AgentFunctionBridgeModel.agent_id == agent.id).all()
    ]
    tools = []
    if bound_function_ids:
        tools = db.session.query(FunctionModel).filter(FunctionModel.id.in_(bound_function_ids)).all()
    el_tool_ids = [t.elevenlabs_tool_id for t in tools if t.elevenlabs_tool_id]
    el_client.update_agent(agent_id=agent.elevenlabs_agent_id, tool_ids=el_tool_ids, prompt=agent.system_prompt)


def ensure_personal_kb_tool(user_id: int) -> None:
    """
    Idempotent. If the user has at least one personal KB item: create the
    system tool if it doesn't exist yet, then make sure every one of the
    user's agents (old and new) has it bound and the managed prompt block
    appended. No-op if the user has no personal KB items.
    """
    with db():
        has_kb_items = db.session.query(PersonalKnowledgeBaseModel.id).filter(
            PersonalKnowledgeBaseModel.user_id == user_id
        ).first() is not None
        if not has_kb_items:
            return

        tool = _get_system_tool(user_id)
        if not tool:
            try:
                tool = _create_system_tool(user_id)
            except Exception as e:
                db.session.rollback()
                logger.error(f"Failed to provision personal KB tool for user {user_id}: {e}")
                return

        agents = db.session.query(AgentModel).filter(AgentModel.user_id == user_id).all()
        el_client = ElevenLabsAgent()
        for agent in agents:
            bridge = db.session.query(AgentFunctionBridgeModel).filter(
                AgentFunctionBridgeModel.agent_id == agent.id,
                AgentFunctionBridgeModel.function_id == tool.id,
            ).first()
            if not bridge:
                db.session.add(AgentFunctionBridgeModel(agent_id=agent.id, function_id=tool.id))
                db.session.flush()

            if _PROMPT_BLOCK_START not in (agent.system_prompt or ""):
                agent.system_prompt = _add_prompt_block(agent.system_prompt or "")

            try:
                _resync_agent(agent, el_client)
            except Exception as e:
                logger.warning(f"Failed to sync personal KB tool to agent {agent.id}: {e}")

        db.session.commit()


def remove_personal_kb_tool_if_empty(user_id: int) -> None:
    """
    If the user has zero personal KB items left: detach the system tool from
    every agent it's bound to, strip the managed prompt block, delete the
    ElevenLabs tool, and delete the FunctionModel row. No-op otherwise.
    """
    with db():
        has_kb_items = db.session.query(PersonalKnowledgeBaseModel.id).filter(
            PersonalKnowledgeBaseModel.user_id == user_id
        ).first() is not None
        if has_kb_items:
            return

        tool = _get_system_tool(user_id)
        if not tool:
            return

        agents = (
            db.session.query(AgentModel)
            .join(AgentFunctionBridgeModel, AgentFunctionBridgeModel.agent_id == AgentModel.id)
            .filter(AgentFunctionBridgeModel.function_id == tool.id)
            .all()
        )

        el_client = ElevenLabsAgent()
        for agent in agents:
            db.session.query(AgentFunctionBridgeModel).filter(
                AgentFunctionBridgeModel.agent_id == agent.id,
                AgentFunctionBridgeModel.function_id == tool.id,
            ).delete()
            agent.system_prompt = _remove_prompt_block(agent.system_prompt or "")
            db.session.flush()

            try:
                _resync_agent(agent, el_client)
            except Exception as e:
                logger.warning(f"Failed to unsync personal KB tool from agent {agent.id}: {e}")

        elevenlabs_tool_id = tool.elevenlabs_tool_id
        db.session.delete(tool)  # cascades FunctionApiConfig + AgentFunctionBridgeModel rows
        db.session.commit()

        if elevenlabs_tool_id:
            try:
                el_client.delete_tool(elevenlabs_tool_id)
            except Exception as e:
                logger.warning(f"Failed to delete ElevenLabs tool {elevenlabs_tool_id}: {e}")
