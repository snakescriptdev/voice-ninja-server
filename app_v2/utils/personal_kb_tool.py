"""
Auto-manages the system "search_personal_knowledge_base" tool. Unlike a
regular Function, this tool is provisioned per-AGENT, not per-user: each
agent that has at least one personal KB item attached gets its own tool
(own ElevenLabs tool object, own webhook URL scoped to that agent's id) so
that at conversation time it only ever searches the KB items attached to
that specific agent — never another agent's, even for the same user.

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
    PersonalKnowledgeBaseAgentBridgeModel,
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
    "and notes) for content relevant to a question, and returns a ready-to-speak "
    "answer already synthesized from the matching documents — just relay it, "
    "don't re-derive your own answer from raw excerpts. Call this tool with the "
    "user's question or topic whenever they ask something that might be "
    "answered by their own uploaded documents or notes. Also pass a brief "
    "summary of the recent conversation relevant to the question, if any — it "
    "helps resolve follow-up questions that depend on earlier context."
)

_PROMPT_BLOCK_START = "<!-- personal_kb_tool:start -->"
_PROMPT_BLOCK_END = "<!-- personal_kb_tool:end -->"
_PROMPT_BLOCK_PATTERN = re.compile(
    re.escape(_PROMPT_BLOCK_START) + r".*?" + re.escape(_PROMPT_BLOCK_END), re.DOTALL
)
_PROMPT_BLOCK_TEXT = (
    f"\n\n{_PROMPT_BLOCK_START}\n"
    f"You have access to a custom knowledge base its accessed from tool of name search_personal_knowledge_base. You are strictly forbidden from answering questions about any query using your own pre-trained knowledge. If the user asks about any topic, you MUST call the search_personal_knowledge_base tool. If the information is not found there, explicitly state that you do not have that information"
    f"{_PROMPT_BLOCK_END}"
)


def _webhook_base_url() -> str:
    # e.g. "https://apis.voiceninja.ai/api/v2" — already includes the
    # "/api/v2" prefix, so callers must not append it again.
    return (VoiceSettings.BE_API_URL or "").rstrip("/")


def strip_prompt_block(system_prompt: str) -> str:
    if not system_prompt:
        return system_prompt or ""
    return _PROMPT_BLOCK_PATTERN.sub("", system_prompt).rstrip()


def _add_prompt_block(system_prompt: str) -> str:
    return strip_prompt_block(system_prompt) + _PROMPT_BLOCK_TEXT


def agent_has_personal_kb_tool(agent_id: int) -> bool:
    """
    Whether this agent currently has at least one personal KB item attached
    (and therefore has its own search_personal_knowledge_base tool + prompt
    block). Must be called with an active db session already in scope —
    does not open its own `with db():` block.
    """
    return db.session.query(PersonalKnowledgeBaseAgentBridgeModel.id).filter(
        PersonalKnowledgeBaseAgentBridgeModel.agent_id == agent_id
    ).first() is not None


def apply_prompt_block_state(agent_id: int, prompt: str) -> str:
    """
    Given a (block-free, user-facing) prompt from a client update, returns it
    with the personal-KB tool prompt block present if this agent currently
    has an active tool, or stripped otherwise — regardless of whether the
    incoming prompt happens to already contain one. Callers should use this
    to build the value actually persisted to `agent.system_prompt` and sent
    to ElevenLabs, so the block survives (or stays absent) independent of
    whatever the client echoes back. Must be called with an active db
    session already in scope.
    """
    if agent_has_personal_kb_tool(agent_id):
        return _add_prompt_block(prompt)
    return strip_prompt_block(prompt)


def _get_agent_system_tool(agent_id: int) -> FunctionModel:
    return (
        db.session.query(FunctionModel)
        .join(AgentFunctionBridgeModel, AgentFunctionBridgeModel.function_id == FunctionModel.id)
        .filter(
            AgentFunctionBridgeModel.agent_id == agent_id,
            FunctionModel.is_system_managed.is_(True),
            FunctionModel.name == TOOL_NAME,
        )
        .first()
    )


def _create_system_tool(user_id: int, agent_id: int) -> FunctionModel:
    secret = VoiceSettings.INTERNAL_API_SECRET_KEY
    if not secret:
        logger.warning("INTERNAL_API_SECRET_KEY is not set — the personal KB tool webhook will be unauthenticated.")
    auth_header = f"Bearer {secret}" if secret else ""

    url = f"{_webhook_base_url()}/personal-knowledge-base/tool-search/{agent_id}"
    body_field = {
        "query": {"type": "string", "description": "The user's question or topic to search for"},
        "conversation_context": {
            "type": "string",
            "description": (
                "A brief summary of recent conversation turns relevant to the "
                "query, if any — helps resolve follow-up questions that "
                "depend on earlier context. Omit if there is none."
            ),
        },
    }

    api_schema = ApiSchema(
        url=url,
        method=HttpMethod.POST,
        request_headers={"Authorization": auth_header},
        content_type=ContentType.JSON,
        request_body_schema=RequestBodySchema(
            type="object",
            properties={
                "query": BodyField(type="string", description=body_field["query"]["description"]),
                "conversation_context": BodyField(type="string", description=body_field["conversation_context"]["description"]),
            },
            required=["query"],
        ),
    )

    el_client = ElevenLabsAgent()
    # "off" — the answer is already synthesized and ready to speak; the agent
    # should relay it directly rather than narrating that it's checking notes
    # or the knowledge base first.
    el_response = el_client.create_tool(name=TOOL_NAME, description=TOOL_DESCRIPTION, api_schema=api_schema, pre_tool_speech="off")
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
        headers={"Authorization": encrypt_data(auth_header)} if secret else {},
        query_params={},
        path_params={},
        body_schema={"type": "object", "properties": body_field, "required": ["query"]},
        response_variables={},
        timeout_ms=30000,
        speak_while_execution=False,
        speak_after_execution=True,
    ))
    db.session.flush()

    logger.info(f"Created personal KB tool for agent {agent_id} (user {user_id}): elevenlabs_tool_id={elevenlabs_tool_id}")
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


def ensure_personal_kb_tool_for_agent(agent_id: int) -> None:
    """
    Idempotent. Call after attaching a personal KB item to an agent: creates
    that agent's own system tool if it doesn't exist yet, binds it, appends
    the managed prompt block, and resyncs the agent to ElevenLabs.
    """
    with db():
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
        if not agent:
            return

        tool = _get_agent_system_tool(agent_id)
        if not tool:
            try:
                tool = _create_system_tool(agent.user_id, agent_id)
            except Exception as e:
                db.session.rollback()
                logger.error(f"Failed to provision personal KB tool for agent {agent_id}: {e}")
                return
            db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=tool.id))
            db.session.flush()

        if _PROMPT_BLOCK_START not in (agent.system_prompt or ""):
            agent.system_prompt = _add_prompt_block(agent.system_prompt or "")

        try:
            _resync_agent(agent, ElevenLabsAgent())
        except Exception as e:
            logger.warning(f"Failed to sync personal KB tool to agent {agent_id}: {e}")

        db.session.commit()


def remove_personal_kb_tool_from_agent_if_empty(agent_id: int) -> None:
    """
    Call after detaching/deleting a personal KB item. If this agent has zero
    personal KB items left attached: unbind & delete its dedicated tool
    (DB row + ElevenLabs object), strip the managed prompt block, and resync.
    No-op otherwise.
    """
    with db():
        has_items = db.session.query(PersonalKnowledgeBaseAgentBridgeModel.id).filter(
            PersonalKnowledgeBaseAgentBridgeModel.agent_id == agent_id
        ).first() is not None
        if has_items:
            return

        tool = _get_agent_system_tool(agent_id)
        if not tool:
            return

        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
        el_client = ElevenLabsAgent()
        if agent:
            db.session.query(AgentFunctionBridgeModel).filter(
                AgentFunctionBridgeModel.agent_id == agent_id,
                AgentFunctionBridgeModel.function_id == tool.id,
            ).delete()
            agent.system_prompt = strip_prompt_block(agent.system_prompt or "")
            db.session.flush()

            try:
                _resync_agent(agent, el_client)
            except Exception as e:
                logger.warning(f"Failed to unsync personal KB tool from agent {agent_id}: {e}")

        elevenlabs_tool_id = tool.elevenlabs_tool_id
        db.session.delete(tool)  # cascades FunctionApiConfig + AgentFunctionBridgeModel rows
        db.session.commit()

        if elevenlabs_tool_id:
            try:
                el_client.delete_tool(elevenlabs_tool_id)
            except Exception as e:
                logger.warning(f"Failed to delete ElevenLabs tool {elevenlabs_tool_id}: {e}")


def resync_personal_kb_tool_for_agent(agent_id: int) -> None:
    """
    Best-effort: if this agent already has its own personal KB tool bound,
    re-push its current tool list/prompt to ElevenLabs. Used after a generic
    agent update that may have overwritten the agent's tool_ids without
    knowing about the system-managed KB tool. No-op if this agent has none.
    """
    with db():
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
        if not agent:
            return
        if not _get_agent_system_tool(agent_id):
            return
        try:
            _resync_agent(agent, ElevenLabsAgent())
        except Exception as e:
            logger.warning(f"Failed to re-sync personal KB tool onto agent {agent_id}: {e}")


def delete_agent_personal_kb_tool(agent_id: int) -> None:
    """
    Unconditionally removes this agent's dedicated personal KB tool (DB row +
    ElevenLabs object), if it has one — regardless of remaining KB
    attachments. Call this right before the agent itself is deleted, so its
    tool doesn't end up orphaned (no agent left to bind it to).
    """
    with db():
        tool = _get_agent_system_tool(agent_id)
        if not tool:
            return

        elevenlabs_tool_id = tool.elevenlabs_tool_id
        db.session.delete(tool)  # cascades FunctionApiConfig + AgentFunctionBridgeModel rows
        db.session.commit()

        if elevenlabs_tool_id:
            try:
                ElevenLabsAgent().delete_tool(elevenlabs_tool_id)
            except Exception as e:
                logger.warning(f"Failed to delete ElevenLabs tool {elevenlabs_tool_id}: {e}")
