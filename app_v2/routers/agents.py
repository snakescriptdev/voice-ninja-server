import re
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from fastapi_sqlalchemy import db
from app_v2.schemas.agent_config import AgentConfigGenerator, AgentConfigOut
from app_v2.schemas.pagination import PaginatedResponse, PageSize
from app_v2.schemas.enum_types import PhoneNumberAssignStatus
import math
from app_v2.utils.llm_utils import generate_system_prompt_async
from app_v2.utils.elevenlabs.agent_utils import ElevenLabsAgent
from app_v2.utils.elevenlabs.kb_utils import ElevenLabsKB
from app_v2.utils.elevenlabs.phone_connection import ElevenLabsPhoneConnection
from app_v2.utils.conversation_lifecycle import is_agents_first_call
from app_v2.utils.coin_utils import coins_to_inr
from app_v2.utils.feature_access import check_can_enable_resource, require_feature_enabled

from app_v2.utils.jwt_utils import HTTPBearer,require_active_user
from app_v2.databases.models import (
    AdminTokenModel,
    VoiceTraitsModel,
    TokensToConsume,
    AgentModel,
    VoiceModel,
    AIModels,
    LanguageModel,
    AgentAIModelBridge,
    AgentLanguageBridge,
    UnifiedAuthModel,
    PhoneNumberService,
    TwilioUserCreds,
    KnowledgeBaseModel,
    AgentKnowledgeBaseBridge,
    PersonalKnowledgeBaseAgentBridgeModel,
    AgentFunctionBridgeModel,
    FunctionModel,
    VariablesModel,
    WidgetModel,
    WebAgentPageModel,
    ConversationsModel,
    WidgetLeadModel,
)
from app_v2.schemas.agent_schema import AgentCreate, AgentRead, AgentUpdate
from app_v2.schemas.llm_pricing import LlmPricingResponse, LlmPriceItem
from app_v2.utils.currency_utils import get_usd_to_inr_rate
from app_v2.schemas.built_in_tools import BuiltInToolsParams
from app_v2.schemas.enum_types import PlanFeatureEnum
from typing import List, Optional, Any
from app_v2.utils.activity_logger import log_activity
from app_v2.core.logger import setup_logger
from app_v2.core.config import VoiceSettings
from app_v2.utils.feature_access import RequireFeature
from app_v2.utils.crypto_utils import decrypt_data
from app_v2.utils.twillio_phone_service import TwilioPhoneService
from app_v2.utils.personal_kb_tool import (
    resync_personal_kb_tool_for_agent,
    delete_agent_personal_kb_tool,
    strip_prompt_block,
    apply_prompt_block_state,
)
from twilio.base.exceptions import TwilioRestException
logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/agent",
    tags=["agent"],
)

security = HTTPBearer()


from sqlalchemy.orm import selectinload

# ... (other imports)

# -------------------- RESPONSE MAPPER --------------------

def agent_to_read(
    agent: AgentModel,
    is_first_call_pending: Optional[bool] = None,
    conversation_count: int = 0,
    amount_used: float = 0,
    leads_count: int = 0,
) -> AgentRead:
    ai_model = (
        agent.agent_ai_models[0].ai_model.model_name
        if agent.agent_ai_models else None
    )
    language = (
        agent.agent_languages[0].language.lang_code
        if agent.agent_languages else None
    )

    phone_number = (
        agent.phone_number[0].phone_number
        if agent.phone_number else None
    )

    # Recompute from the live prompt (rather than trusting stored rows as-is)
    # so agents predating this feature, or any drift between the prompt and
    # persisted variables, still resolve correctly on read. system__ vars are
    # excluded — they're ElevenLabs built-ins auto-populated at call time,
    # never custom variables the user manages.
    stored_variables = {v.variable_name: v.variable_value for v in agent.variables}
    variables = {
        name: stored_variables.get(name, "")
        for name in extract_prompt_variable_names(agent.system_prompt)
    }

    return AgentRead(
        id=agent.id,
        agent_name=agent.agent_name,
        is_enabled=agent.is_enabled,
        first_message=agent.first_message,
        # The personal-KB tool prompt block is an implementation detail the
        # user never typed and shouldn't see/edit — hidden here, reapplied
        # on write via apply_prompt_block_state() if the agent still has an
        # active tool (see update_agent below).
        system_prompt=strip_prompt_block(agent.system_prompt),
        voice=agent.voice.voice_name,
        ai_model=ai_model,
        language=language,
        updated_at=agent.modified_at.date(),
        elevenlabs_agent_id=agent.elevenlabs_agent_id,
        phone=phone_number,
        knowledgebase = [
            {
                "id": bridge.knowledge_base.id,
                "title": bridge.knowledge_base.title,
                "type": bridge.knowledge_base.kb_type
            }
            for bridge in agent.agent_knowledge_bases
        ],
        variables=variables,
        tools=[
            {
                "id": bridge.function.id,
                "name": bridge.function.name
            }
            for bridge in agent.agent_functions
        ],
        built_in_tools=agent.built_in_tools,
        timezone=agent.timezone,
        is_first_call_pending=(
            is_first_call_pending
            if is_first_call_pending is not None
            else is_agents_first_call(agent.id)
        ),
        kb_count=len(agent.personal_kb_agent_bridges),
        tool_count=len(agent.agent_functions),
        conversation_count=conversation_count,
        amount_used=amount_used,
        leads_count=leads_count,
    )


# -------------------- HELPERS --------------------

# Matches {{var_name}} placeholders in a prompt. "system__*" placeholders are
# ElevenLabs built-in variables (e.g. system__time_utc) that are always
# available and auto-populated by ElevenLabs at conversation time — they
# must NOT be declared in dynamic_variable_placeholders (doing so would
# submit an empty value that overrides the real runtime value), so they're
# excluded whenever include_system=False.
PROMPT_VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def extract_prompt_variable_names(*texts: Optional[str], include_system: bool = False) -> set[str]:
    """Extracts {{var_name}} placeholder names from prompt text(s)."""
    names = set()
    for text in texts:
        if not text:
            continue
        names.update(PROMPT_VARIABLE_PATTERN.findall(text))
    if include_system:
        return names
    return {name for name in names if not name.startswith("system__")}


# timezone is only required when the prompt actually renders time in it —
# otherwise ElevenLabs has nothing to localize and the field is optional.
TIMEZONE_REQUIRING_VARS = {"system__time_utc", "system__time", "system__timezone"}


def prompt_requires_timezone(prompt: Optional[str]) -> bool:
    """True if the prompt references a system time/timezone placeholder, making `timezone` mandatory."""
    return bool(extract_prompt_variable_names(prompt, include_system=True) & TIMEZONE_REQUIRING_VARS)


def transform_built_in_tools(built_in_tools_params, session: Session, user_id: int, current_agent_id: Optional[int] = None) -> dict:
    """Transform schema params to ElevenLabs payload structure."""
    if not built_in_tools_params:
        return None
        
    el_tools = {}
    
    # End Call
    if built_in_tools_params.end_call:
        config = built_in_tools_params.end_call
        if isinstance(config, bool):
            el_tools["end_call"] = {
                "name": "end_call",
                "params": {"system_tool_type": "end_call"}
            }
        else: # ToolConfig object
            el_tools["end_call"] = {
                "name": config.name or "end_call",
                "params": {"system_tool_type": "end_call"}
            }

    # Transfer to Agent
    if built_in_tools_params.transfer_to_agent:
        config = built_in_tools_params.transfer_to_agent
        if config.enabled:
            el_transfers = []
            valid_transfers = []
            seen_transfers = set()
            for t in config.transfers:
                transfer_data = t.model_dump()
                requested_id = str(transfer_data.get("agent_id"))

                # Enforce numeric ID for internal lookups
                if not requested_id.isdigit():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Agent ID '{requested_id}' must be an internal numeric ID for transfer to agent tool"
                    )

                # An agent can't transfer a call to itself
                if current_agent_id is not None and int(requested_id) == current_agent_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="An agent cannot be configured to transfer a call to itself"
                    )

                # Dynamic lookup: find agent by internal ID
                target_agent = session.query(AgentModel).filter(
                    AgentModel.id == int(requested_id),
                    AgentModel.user_id == user_id
                ).first()

                # Duplicate detection (same target + condition) happens here,
                # after the lookup, so the error can name the agent instead of
                # showing its raw internal id.
                dup_key = (requested_id, t.condition.strip().lower())
                if dup_key in seen_transfers:
                    agent_label = target_agent.agent_name if target_agent else requested_id
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Duplicate transfer to agent '{agent_label}' with the same condition '{t.condition}'"
                    )
                seen_transfers.add(dup_key)

                if target_agent and target_agent.elevenlabs_agent_id:
                    transfer_data["agent_id"] = target_agent.elevenlabs_agent_id
                    logger.info(f"Resolved agent transfer ID: {requested_id} -> {target_agent.elevenlabs_agent_id}")
                else:
                    logger.info(f"NOT FOUND agent transfer ID: {requested_id}, dropping this transfer")
                    continue

                el_transfers.append(transfer_data)
                valid_transfers.append(t)

            # Drop invalid transfers from the source config too, so the caller
            # persists the same set it just sent to ElevenLabs instead of
            # keeping stale/unresolvable agent_id references in the DB.
            config.transfers = valid_transfers

            if valid_transfers:
                el_tools["transfer_to_agent"] = {
                    "name": config.name or "transfer_to_agent",
                    "params": {
                        "system_tool_type": "transfer_to_agent",
                        "transfers": el_transfers
                    }
                }
            else:
                # None of the configured transfers resolved to a real agent —
                # don't send the tool to ElevenLabs, and mark it disabled in
                # what gets persisted so the frontend doesn't show transfer to
                # agent as enabled with nothing to transfer to.
                logger.info("No valid transfers remain for transfer_to_agent; disabling the tool")
                config.enabled = False
            
    # Transfer to Number
    if built_in_tools_params.transfer_to_number:
        config = built_in_tools_params.transfer_to_number
        if config.enabled:
            el_transfers = []
            for t in config.transfers:
                transfer_data = t.model_dump()
                phone_number = transfer_data.get("transfer_destination", {}).get("phone_number")
                
                # Ownership verification: ensure number belongs to user and PhoneNumberService
                db_phone = session.query(PhoneNumberService).filter(
                    PhoneNumberService.phone_number == phone_number,
                    PhoneNumberService.user_id == user_id
                ).first()
                
                if not db_phone:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Phone number '{phone_number}' does not belong to your account for transfer to number tool"
                    )
                
                el_transfers.append(transfer_data)

            el_tools["transfer_to_number"] = {
                "name": config.name or "transfer_to_number",
                "params": {
                    "system_tool_type": "transfer_to_number",
                    "transfers": el_transfers
                }
            }

    # DTMF / Keypad
    if built_in_tools_params.play_keypad_touch_tone:
        require_feature_enabled(user_id, PlanFeatureEnum.phone_numbers)
        config = built_in_tools_params.play_keypad_touch_tone
        if isinstance(config, bool):
             el_tools["play_keypad_touch_tone"] = {
                "name": "play_keypad_touch_tone",
                "params": {"system_tool_type": "play_keypad_touch_tone"}
            }
        else:
             el_tools["play_keypad_touch_tone"] = {
                "name": config.name or "play_keypad_touch_tone",
                "params": {"system_tool_type": "play_keypad_touch_tone"}
            }

    return el_tools if el_tools else None


def prune_stale_agent_transfers(agent: AgentModel, session: Session) -> None:
    """
    Re-validate agent.built_in_tools.transfer_to_agent on read: drop any transfer
    whose target agent was deleted (or lost its elevenlabs_agent_id) since this
    config was last saved, persist the cleaned config, and push the same cleanup
    to ElevenLabs so a deleted agent can't linger as a transfer target.

    Deliberately self-contained (doesn't call transform_built_in_tools) — that
    function can raise HTTPException for unrelated reasons (e.g. a stale
    transfer_to_number phone), which must never surface on a plain read.
    """
    tta = (agent.built_in_tools or {}).get("transfer_to_agent")
    transfers = (tta or {}).get("transfers") or []
    if not transfers:
        return

    requested_ids = {str(t.get("agent_id")) for t in transfers if str(t.get("agent_id")).isdigit()}
    live_ids = set()
    if requested_ids:
        live_agents = session.query(AgentModel.id).filter(
            AgentModel.id.in_([int(rid) for rid in requested_ids]),
            AgentModel.user_id == agent.user_id,
            AgentModel.elevenlabs_agent_id.isnot(None),
            AgentModel.id != agent.id,
        ).all()
        live_ids = {str(row.id) for row in live_agents}

    valid_transfers = [t for t in transfers if str(t.get("agent_id")) in live_ids]
    if len(valid_transfers) == len(transfers):
        return

    stale_ids = requested_ids - live_ids
    logger.info(f"Pruning stale transfer_to_agent targets {stale_ids} from agent {agent.id}")

    new_built_in_tools = dict(agent.built_in_tools)
    new_built_in_tools["transfer_to_agent"] = {
        **tta,
        "transfers": valid_transfers,
        "enabled": tta.get("enabled", False) and bool(valid_transfers),
    }
    agent.built_in_tools = new_built_in_tools

    if agent.elevenlabs_agent_id:
        try:
            parsed = BuiltInToolsParams(**new_built_in_tools)
            el_payload = transform_built_in_tools(parsed, session, agent.user_id, current_agent_id=agent.id)
            el_response = ElevenLabsAgent().update_agent(
                agent_id=agent.elevenlabs_agent_id,
                built_in_tools=el_payload,
            )
            if not el_response.status:
                logger.error(
                    f"Failed to sync pruned transfers to ElevenLabs for agent {agent.id}: {el_response.error_message}"
                )
        except Exception:
            logger.exception(f"Unexpected error syncing pruned transfers to ElevenLabs for agent {agent.id}")

    session.commit()


def resolve_phone_record(
    session: Session,
    user_id: int,
    phone: Optional[str],
    twilio_connector_id: Optional[int],
    current_agent_id: Optional[int] = None,
):
    """
    Validate & resolve the PhoneNumberService row for `phone`, without assigning
    it to an agent yet (the caller does that once the agent id is known).

    If `twilio_connector_id` is given, the number is verified against that Twilio
    account via the Twilio API and the local record is created/updated on the fly.
    Otherwise falls back to requiring an already-owned, previously imported number.

    Returns (phone_record_or_None, connector_or_None). Raises HTTPException on
    any validation failure (feature not enabled, connector not found, number not
    found in Twilio, number already assigned to a different agent).
    """
    if not phone or not phone.strip():
        return None, None
    # Normalize away any internal/leading/trailing whitespace (e.g. "+1 659 399 7159"
    # -> "+16593997159") so numbers are matched and stored consistently regardless
    # of how the user typed them.
    phone = re.sub(r"\s+", "", phone)

    connector = None
    if twilio_connector_id:
        require_feature_enabled(user_id, PlanFeatureEnum.phone_numbers)

        connector = session.query(TwilioUserCreds).filter(
            TwilioUserCreds.id == twilio_connector_id,
            TwilioUserCreds.user_id == user_id,
        ).first()
        if not connector:
            raise HTTPException(status_code=404, detail="Twilio connector not found")

        twilio_number = None
        try:
            service = TwilioPhoneService(
                account_sid=connector.account_sid,
                auth_token=decrypt_data(connector.auth_token),
            )
            twilio_number = service.get_phone_number_details(phone)
        except TwilioRestException as te:
            logger.warning(f"Twilio verification failed for {phone}: {te}")

        if not twilio_number:
            raise HTTPException(
                status_code=400,
                detail=f"Phone number {phone} was not found in the selected Twilio connector."
            )

        phone_record = session.query(PhoneNumberService).filter(
            PhoneNumberService.phone_number == phone,
            PhoneNumberService.user_id == user_id,
        ).first()
        if phone_record:
            phone_record.sid = twilio_number["sid"]
        else:
            phone_record = PhoneNumberService(
                phone_number=phone,
                sid=twilio_number["sid"],
                type="connector",
                user_id=user_id,
                assigned_to=None,
                status=PhoneNumberAssignStatus.unassigned,
                monthly_cost=0,
            )
            session.add(phone_record)
            session.flush()
    else:
        phone_record = session.query(PhoneNumberService).filter(
            PhoneNumberService.phone_number == phone,
            PhoneNumberService.user_id == user_id,
        ).first()
        if not phone_record:
            raise HTTPException(
                status_code=404,
                detail=f"Phone number {phone} not found or not owned by you"
            )

    # Global uniqueness: no two agents can ever share the same Twilio number.
    if phone_record.assigned_to is not None and phone_record.assigned_to != current_agent_id:
        raise HTTPException(
            status_code=400,
            detail=f"Phone number {phone} is already assigned to another agent"
        )

    return phone_record, connector


def finalize_phone_assignment(phone_record: Optional[PhoneNumberService], agent: AgentModel, connector) -> None:
    """Assign `phone_record` to `agent` and best-effort register/relink it with ElevenLabs."""
    if not phone_record:
        return

    phone_record.assigned_to = agent.id
    phone_record.status = PhoneNumberAssignStatus.assigned
    logger.info(f"Assigned phone {phone_record.phone_number} to agent {agent.agent_name}")

    if not agent.elevenlabs_agent_id:
        return

    def _import_fresh(el: ElevenLabsPhoneConnection) -> None:
        account_sid = connector.account_sid if connector else VoiceSettings.TWILIO_ACCOUNT_SID
        auth_token = decrypt_data(connector.auth_token) if connector else VoiceSettings.TWILIO_AUTH_TOKEN
        resp = el.import_twilio_number(
            phone_number=phone_record.phone_number,
            label=agent.agent_name,
            account_sid=account_sid,
            auth_token=auth_token,
            agent_id=agent.elevenlabs_agent_id,
        )
        if resp.status and resp.data:
            phone_record.elevenlabs_phone_id = resp.data.get("phone_number_id")
        else:
            logger.warning(f"ElevenLabs phone import failed for {phone_record.phone_number}: {resp.error_message}")

    try:
        el = ElevenLabsPhoneConnection()
        if phone_record.elevenlabs_phone_id:
            resp = el.update_phone_number_agent(phone_record.elevenlabs_phone_id, agent.elevenlabs_agent_id)
            if not resp.status:
                # The stored elevenlabs_phone_id no longer exists on ElevenLabs
                # (e.g. it was deleted when previously unassigned) — clear it and
                # re-import the number fresh instead of silently leaving it unlinked.
                logger.warning(
                    f"Failed to relink ElevenLabs phone number {phone_record.elevenlabs_phone_id}: "
                    f"{resp.error_message}; re-importing {phone_record.phone_number} fresh"
                )
                phone_record.elevenlabs_phone_id = None
                _import_fresh(el)
        else:
            _import_fresh(el)
    except Exception as e:
        logger.error(f"ElevenLabs phone registration failed for {phone_record.phone_number}: {e}", exc_info=True)


def unassign_phone(phone_record: Optional[PhoneNumberService]) -> None:
    """Unassign a phone record from its agent and best-effort delete it from ElevenLabs."""
    if not phone_record:
        return
    phone_record.assigned_to = None
    phone_record.status = PhoneNumberAssignStatus.unassigned
    if phone_record.elevenlabs_phone_id:
        try:
            response = ElevenLabsPhoneConnection().delete_phone_number(phone_record.elevenlabs_phone_id)
            if response.status:
                phone_record.elevenlabs_phone_id = None
            else:
                logger.warning(
                    f"Failed to delete ElevenLabs phone number {phone_record.elevenlabs_phone_id}: {response.error_message}"
                )
        except Exception as e:
            logger.warning(f"Failed to delete ElevenLabs phone number {phone_record.elevenlabs_phone_id}: {e}")


def unassign_phone_numbers_for_connector(session: Session, user_id: int, connector: TwilioUserCreds) -> None:
    """
    Best-effort: when a Twilio connector is deleted, unassign every phone number
    provisioned under that connector's Twilio account from whichever agent it's
    linked to, both in the DB (`assigned_to`) and in ElevenLabs.

    There's no persistent FK from PhoneNumberService to TwilioUserCreds (the
    connector is only used transiently to verify/import a number), so the set of
    numbers belonging to this connector is resolved by listing the connector's
    Twilio account and matching against locally stored "connector" type records.
    """
    try:
        service = TwilioPhoneService(
            account_sid=connector.account_sid,
            auth_token=decrypt_data(connector.auth_token),
        )
        connector_numbers = set(service.list_account_phone_numbers())
    except TwilioRestException as te:
        logger.warning(f"Could not list Twilio numbers for connector_id={connector.id}: {te}")
        return

    if not connector_numbers:
        return

    phone_records = session.query(PhoneNumberService).filter(
        PhoneNumberService.user_id == user_id,
        PhoneNumberService.type == "connector",
        PhoneNumberService.phone_number.in_(connector_numbers),
        PhoneNumberService.assigned_to.isnot(None),
    ).all()

    for phone_record in phone_records:
        logger.info(
            f"Unassigning phone {phone_record.phone_number} from agent_id={phone_record.assigned_to} "
            f"due to deletion of connector_id={connector.id}"
        )
        unassign_phone(phone_record)


# -------------------- CREATE --------------------

@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Create agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def create_agent(
    agent_in: AgentCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeature("ai_voice_agents", allow_coin_fallback=True)),
):
    user_id = current_user.id
    
    #removed the name uniqueness constraint may switch in future

    # #check for agent existence 
    agent_exists = (
        db.session.query(AgentModel).filter(
            func.lower(AgentModel.agent_name) == agent_in.agent_name.lower(),
            AgentModel.user_id == user_id
        ).first()
    )

    if agent_exists:
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail= "Agent with this name already exists"
        )

    # -------------------------------------------------
    # Voice validation: only allow voices that are synced with ElevenLabs
    # -------------------------------------------------
    voice = (
        db.session.query(VoiceModel)
        .filter(
            VoiceModel.voice_name == agent_in.voice,
            VoiceModel.elevenlabs_voice_id.isnot(None),
            or_(
                VoiceModel.is_custom_voice.is_(False),
                VoiceModel.user_id == user_id,
            ),
        )
        .first()
    )

    if not voice:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Voice '{agent_in.voice}' not found or not synced",
                "hint": "Run the voice sync script to sync voices, then use a voice from the list.",
            },
        )
    if not voice.is_enabled:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Voice '{agent_in.voice}' is not enabled",
            },
        )

    # -------------------------------------------------
    # AI Model validation (single)
    # -------------------------------------------------
    ai_model = (
        db.session.query(AIModels)
        .filter(AIModels.model_name == agent_in.ai_model)
        .first()
    )

    if not ai_model:
        raise HTTPException(status_code=400, detail="Invalid AI model")

    # -------------------------------------------------
    # Language validation (single)
    # -------------------------------------------------
    language = (
        db.session.query(LanguageModel)
        .filter(LanguageModel.lang_code == agent_in.language)
        .first()
    )

    if not language:
        raise HTTPException(status_code=400, detail="Invalid language code")

    # -------------------------------------------------
    # Phone number lookup & validation
    # -------------------------------------------------
    phone_record, phone_connector = resolve_phone_record(
        db.session, user_id, agent_in.phone, agent_in.twilio_connector_id
    )

    # -------------------------------------------------
    # KB & Tools validation and lookup
    # -------------------------------------------------
    el_kb_list = []
    kb_ids_ordered = []
    
    if agent_in.knowledgebase:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
        kb_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order
        
        # 2. Fetch from DB
        kb_records = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id.in_(kb_ids_ordered),
            KnowledgeBaseModel.user_id == user_id,
            KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
        ).all()
        
        # 3. Create a map for O(1) lookup
        kb_map = {kb.id: kb for kb in kb_records}
        
        # 4. Validate all IDs exist (checking against the unique set of requested IDs)
        found_ids = set(kb_map.keys())
        missing_ids = set(kb_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Knowledge Base IDs not found or not synced: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        for kb_id in kb_ids_ordered:
            kb = kb_map[kb_id]
            el_kb_list.append({
                "id": kb.elevenlabs_document_id,
                "type": "file", # ElevenLabs conversational AI usually treats them as files
                "name": kb.title or f"KB_{kb.id}"
            })

    el_tool_ids = []
    tool_ids_ordered = []

    if agent_in.tools:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
        tool_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order

        # 2. Fetch from DB
        tool_records = db.session.query(FunctionModel).filter(
            FunctionModel.id.in_(tool_ids_ordered),
            FunctionModel.elevenlabs_tool_id.isnot(None),
            or_(
                FunctionModel.user_id == user_id,
                FunctionModel.user_id.is_(None)
            )
        ).all()
        
        # 3. Create a map for O(1) lookup
        tool_map = {tool.id: tool for tool in tool_records}

        # 4. Validate all IDs exist
        found_ids = set(tool_map.keys())
        missing_ids = set(tool_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Tool IDs not found or not synced or not accessible to you: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        for tool_id in tool_ids_ordered:
            tool = tool_map[tool_id]
            el_tool_ids.append(tool.elevenlabs_tool_id)

    # -------------------------------------------------
    # Merge explicit variables with {{var_name}} placeholders found in the
    # system prompt, so anything referenced there gets persisted even if the
    # caller didn't declare it explicitly.
    # -------------------------------------------------
    merged_variables = dict(agent_in.variables or {})
    for var_name in extract_prompt_variable_names(agent_in.system_prompt):
        merged_variables.setdefault(var_name, "")

    if prompt_requires_timezone(agent_in.system_prompt) and not agent_in.timezone:
        raise HTTPException(
            status_code=400,
            detail="timezone is required when the system prompt uses {{system__time}}, {{system__time_utc}}, or {{system__timezone}}"
        )

    # -------------------------------------------------
    # Create agent in ElevenLabs (only after validation)
    # -------------------------------------------------
    elevenlabs_agent_id = None
    el_client = ElevenLabsAgent()

    try:
        logger.info(
            f"Creating agent '{agent_in.agent_name}' in ElevenLabs for user {user_id}"
        )

        el_response = el_client.create_agent(
            name=agent_in.agent_name,
            voice_id=voice.elevenlabs_voice_id,
            prompt=agent_in.system_prompt,
            first_message=agent_in.first_message or "Hello! How can I help you?",
            language=language.lang_code,
            llm_model=ai_model.model_name,
            tool_ids=el_tool_ids,
            knowledge_base=el_kb_list,
            dynamic_variables=merged_variables,
            built_in_tools=transform_built_in_tools(agent_in.built_in_tools, db.session, user_id),
            timezone=agent_in.timezone
        )

        if not el_response.status:
            raise HTTPException(
                status_code=424,
                detail=el_response.error_message or "Failed to create agent",
            )

        elevenlabs_agent_id = el_response.data.get("agent_id")
        logger.info(f"✅ ElevenLabs agent created: {elevenlabs_agent_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected ElevenLabs error")
        raise HTTPException(
            status_code=424,
            detail=f"Unexpected error while creating agent: {str(e)}",
        )

    # -------------------------------------------------
    # Database creation (atomic)
    # -------------------------------------------------
    try:
        new_agent = AgentModel(
            agent_name=agent_in.agent_name,
            first_message=agent_in.first_message,
            system_prompt=agent_in.system_prompt,
            user_id=user_id,
            agent_voice=voice.id,
            elevenlabs_agent_id=elevenlabs_agent_id,
            built_in_tools=agent_in.built_in_tools.model_dump() if agent_in.built_in_tools else {},
            timezone=agent_in.timezone
        )

        db.session.add(new_agent)
        db.session.flush()

        # Store the expected per-minute LLM price for this agent's model
        # (best-effort; used only for the live low-balance cutoff estimate).
        new_agent.llm_price_per_minute = el_client.get_llm_price_per_minute(
            elevenlabs_agent_id, ai_model.model_name
        )

        # Cache LLM-cost calibration constants (best-effort; see
        # ConversationsModel's calibration snapshot columns for how these get
        # used per-call). rag.enabled is always sent as True to ElevenLabs
        # (see ElevenLabsAgent.create_agent), so this just records that.
        new_agent.rag_enabled = True
        if el_kb_list:
            new_agent.kb_total_pages = ElevenLabsKB().get_kb_total_pages(elevenlabs_agent_id)
        else:
            new_agent.kb_total_pages = 0

        # Bridge: AI Model
        db.session.add(
            AgentAIModelBridge(
                agent_id=new_agent.id,
                ai_model_id=ai_model.id,
            )
        )

        # Bridge: Language
        db.session.add(
            AgentLanguageBridge(
                agent_id=new_agent.id,
                lang_id=language.id,
            )
        )

        # Bridge: Knowledge Base
        for kb_id in kb_ids_ordered:
            db.session.add(AgentKnowledgeBaseBridge(agent_id=new_agent.id, kb_id=kb_id))

        # Bridge: Tools
        for tool_id in tool_ids_ordered:
            db.session.add(AgentFunctionBridgeModel(agent_id=new_agent.id, function_id=tool_id))

        # Variables (explicit + auto-detected {{placeholders}} from the system prompt)
        for key, value in merged_variables.items():
            db.session.add(VariablesModel(agent_id=new_agent.id, variable_name=key, variable_value=value))

        finalize_phone_assignment(phone_record, new_agent, phone_connector)

        db.session.commit()
        db.session.refresh(new_agent)

        log_activity(
            user_id=user_id,
            event_type="agent_created",
            description=f"Created agent: {new_agent.agent_name}",
            metadata={"agent_id": new_agent.id, "elevenlabs_agent_id": elevenlabs_agent_id}
        )
    except Exception as db_error:
        db.session.rollback()
        if elevenlabs_agent_id:
            try:
                el_client.delete_agent(elevenlabs_agent_id)
                logger.info(f"Cleaned up ElevenLabs agent {elevenlabs_agent_id} after DB failure")
            except Exception as cleanup_err:
                logger.warning(f"Failed to delete orphan ElevenLabs agent {elevenlabs_agent_id}: {cleanup_err}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save agent: {str(db_error)}",
        )

    return agent_to_read(new_agent)


@router.post(
    "/{agent_id}/clone",
    status_code=status.HTTP_201_CREATED,
    summary="Clone agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def clone_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeature("ai_voice_agents", allow_coin_fallback=True)),
):
    """
    Duplicate an existing agent into a brand-new agent (and a fresh ElevenLabs
    agent), copying its prompt, first message, voice, AI model, language,
    knowledge bases, tools, variables, built-in tools and timezone.

    The phone assignment, conversations, widget and web-agent pages are NOT
    copied. Implemented by rebuilding an AgentCreate from the source and
    reusing create_agent(), so all validation / ElevenLabs / DB steps are
    shared with normal creation.
    """
    user_id = current_user.id

    source = (
        db.session.query(AgentModel)
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice),
            selectinload(AgentModel.variables),
            selectinload(AgentModel.agent_knowledge_bases),
            selectinload(AgentModel.agent_functions).selectinload(AgentFunctionBridgeModel.function),
        )
        .filter(AgentModel.id == agent_id, AgentModel.user_id == user_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Agent not found")

    ai_model = source.agent_ai_models[0].ai_model.model_name if source.agent_ai_models else None
    language = source.agent_languages[0].language.lang_code if source.agent_languages else None
    if not ai_model or not language or not source.voice:
        raise HTTPException(
            status_code=400,
            detail="Source agent is missing voice/model/language and cannot be cloned",
        )

    # Generate a unique clone name: "<name> (copy)", "<name> (copy) 2", ...
    base_name = f"{source.agent_name} (copy)"
    clone_name = base_name
    suffix = 2
    while (
        db.session.query(AgentModel)
        .filter(
            func.lower(AgentModel.agent_name) == clone_name.lower(),
            AgentModel.user_id == user_id,
        )
        .first()
    ):
        clone_name = f"{base_name} {suffix}"
        suffix += 1

    # The personal KB search tool is provisioned per-agent (its webhook URL
    # and searchable content are scoped to the source agent specifically), so
    # it can't just be copied onto the clone — exclude it and its prompt
    # block; the clone gets its own once a KB item is attached to it.
    payload = AgentCreate(
        agent_name=clone_name,
        first_message=source.first_message,
        system_prompt=strip_prompt_block(source.system_prompt),
        phone=None,               # unique per-agent assignment — never cloned
        twilio_connector_id=None,
        voice=source.voice.voice_name,
        ai_model=ai_model,
        language=language,
        knowledgebase=[{"id": b.kb_id} for b in source.agent_knowledge_bases],
        variables={v.variable_name: v.variable_value for v in source.variables},
        tools=[{"id": b.function_id} for b in source.agent_functions if not b.function.is_system_managed],
        built_in_tools=BuiltInToolsParams(**source.built_in_tools) if source.built_in_tools else None,
        timezone=source.timezone,
    )

    return await create_agent(agent_in=payload, current_user=current_user)


# -------------------- CHECK NAME AVAILABILITY --------------------
# Lightweight lookup so the frontend can surface "name already exists" as soon
# as the user enters/blurs the name field, instead of only at final submit
# (used by the multi-step Personal Assistant flow — see AgentBasics.tsx).

@router.get(
    "/check-name",
    summary="Check whether an agent name is already taken by the current user",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def check_agent_name(
    name: str,
    exclude_agent_id: Optional[int] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    trimmed = (name or "").strip()
    if not trimmed:
        return {"exists": False}

    query = db.session.query(AgentModel).filter(
        func.lower(AgentModel.agent_name) == trimmed.lower(),
        AgentModel.user_id == current_user.id,
    )
    if exclude_agent_id:
        query = query.filter(AgentModel.id != exclude_agent_id)

    return {"exists": query.first() is not None}


# -------------------- GET ALL --------------------

@router.get(
    "/",
    response_model=PaginatedResponse[AgentRead],
    summary="Get all agents",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_all_agents(
    page: int = Query(1, ge=1),
    size: PageSize = 10,
    name: Optional[str] = None,
    voice: Optional[str] = None,
    sort_by: Optional[str] = None,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    if page < 1:
        page = 1

    skip = (page - 1) * size

    query = (
        db.session.query(AgentModel)
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice),
            selectinload(AgentModel.phone_number),
            selectinload(AgentModel.variables),
            selectinload(AgentModel.agent_knowledge_bases),
            selectinload(AgentModel.personal_kb_agent_bridges),
            selectinload(AgentModel.agent_functions)
        )
        .filter(AgentModel.user_id == current_user.id)
    )

    if name:
        query = query.filter(AgentModel.agent_name.ilike(f"%{name}%"))

    if voice:
        query = query.join(AgentModel.voice).filter(VoiceModel.voice_name.ilike(f"%{voice}%"))

    # sort_by requires aggregate columns (KB/tool/credits counts) that live on
    # other tables — join in grouped subqueries (same shape as
    # app_v2/utils/agent_summary.py) so ORDER BY happens in SQL, before
    # LIMIT/OFFSET, instead of sorting only the current page in Python.
    if sort_by in {"credits_desc", "kb_count_desc", "tool_count_desc"}:
        kb_sub = (
            db.session.query(
                PersonalKnowledgeBaseAgentBridgeModel.agent_id.label("agent_id"),
                func.count(PersonalKnowledgeBaseAgentBridgeModel.kb_id).label("cnt"),
            )
            .group_by(PersonalKnowledgeBaseAgentBridgeModel.agent_id)
            .subquery()
        )
        tool_sub = (
            db.session.query(
                AgentFunctionBridgeModel.agent_id.label("agent_id"),
                func.count(AgentFunctionBridgeModel.function_id).label("cnt"),
            )
            .group_by(AgentFunctionBridgeModel.agent_id)
            .subquery()
        )
        conv_sub = (
            db.session.query(
                ConversationsModel.agent_id.label("agent_id"),
                func.coalesce(func.sum(ConversationsModel.cost_inr), 0).label("amount_used"),
            )
            .group_by(ConversationsModel.agent_id)
            .subquery()
        )
        query = (
            query
            .outerjoin(kb_sub, AgentModel.id == kb_sub.c.agent_id)
            .outerjoin(tool_sub, AgentModel.id == tool_sub.c.agent_id)
            .outerjoin(conv_sub, AgentModel.id == conv_sub.c.agent_id)
        )
        if sort_by == "credits_desc":
            query = query.order_by(func.coalesce(conv_sub.c.amount_used, 0).desc(), AgentModel.agent_name)
        elif sort_by == "kb_count_desc":
            query = query.order_by(func.coalesce(kb_sub.c.cnt, 0).desc(), AgentModel.agent_name)
        elif sort_by == "tool_count_desc":
            query = query.order_by(func.coalesce(tool_sub.c.cnt, 0).desc(), AgentModel.agent_name)
    elif sort_by == "date_added_desc":
        query = query.order_by(AgentModel.created_at.desc())
    else:
        query = query.order_by(AgentModel.modified_at.desc())

    total = query.count()
    pages = math.ceil(total / size)

    agents = (
        query
        .offset(skip)
        .limit(size)
        .all()
    )

    # Bulk-check which of this page's agents have ever had a conversation, so
    # is_first_call_pending doesn't cost a query per agent (N+1) in the list view.
    # Also bulk-fetch per-agent conversation count + credits used for display,
    # same "one grouped query scoped to this page's agent_ids" approach.
    agent_ids = [a.id for a in agents]
    agents_with_calls = (
        {
            row[0]
            for row in db.session.query(ConversationsModel.agent_id)
            .filter(ConversationsModel.agent_id.in_(agent_ids))
            .distinct()
            .all()
        }
        if agent_ids
        else set()
    )
    conversation_stats = (
        {
            row[0]: (row[1], row[2])
            for row in db.session.query(
                ConversationsModel.agent_id,
                func.count(ConversationsModel.id),
                func.coalesce(func.sum(ConversationsModel.cost_inr), 0),
            )
            .filter(ConversationsModel.agent_id.in_(agent_ids))
            .group_by(ConversationsModel.agent_id)
            .all()
        }
        if agent_ids
        else {}
    )
    # Leads aren't linked to agents directly — they hang off the widget that
    # captured them (WidgetLeadModel.widget_id -> WidgetModel.agent_id) — so
    # bulk-count via that join, same page-scoped-groupby shape as above.
    leads_stats = (
        {
            row[0]: row[1]
            for row in db.session.query(
                WidgetModel.agent_id,
                func.count(WidgetLeadModel.id),
            )
            .join(WidgetModel, WidgetLeadModel.widget_id == WidgetModel.id)
            .filter(WidgetModel.agent_id.in_(agent_ids))
            .group_by(WidgetModel.agent_id)
            .all()
        }
        if agent_ids
        else {}
    )
    items = [
        agent_to_read(
            agent,
            is_first_call_pending=(agent.id not in agents_with_calls),
            conversation_count=conversation_stats.get(agent.id, (0, 0))[0],
            amount_used=round(float(conversation_stats.get(agent.id, (0, 0))[1] or 0), 2),
            leads_count=leads_stats.get(agent.id, 0),
        )
        for agent in agents
    ]

    return PaginatedResponse(
        total=total,
        page=page,
        size=size,
        pages=pages,
        items=items
    )


# -------------------- LLM PRICING --------------------

@router.get(
    "/{agent_id}/llm-pricing",
    response_model=LlmPricingResponse,
    summary="Get per-minute price (USD + INR) for every supported LLM, for this agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def get_llm_pricing(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    """
    Calls ElevenLabs' per-agent pricing endpoint
    (POST /convai/agent/{agent_id}/llm-usage/calculate), which derives
    prompt_length / number_of_pages / rag_enabled from the agent's own last-
    saved ElevenLabs config — used to power the "AI Model" picker so users
    can compare cost before choosing a model.

    Requires the agent to already have an elevenlabs_agent_id, i.e. to have
    been saved at least once (the create-agent form autosaves as soon as
    name/prompt/voice/model/language are filled, so this is only briefly
    unavailable right after opening a brand-new agent).
    """
    agent = (
        db.session.query(AgentModel)
        .filter(AgentModel.id == agent_id, AgentModel.user_id == current_user.id)
        .first()
    )
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if not agent.elevenlabs_agent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent has not been saved yet",
        )

    response = ElevenLabsAgent().calculate_llm_usage(agent.elevenlabs_agent_id)

    if not response.status:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch LLM pricing",
        )

    usd_to_inr_rate = get_usd_to_inr_rate()
    llm_prices = [
        LlmPriceItem(
            llm=item["llm"],
            price_per_minute_usd=item["price_per_minute"],
            price_per_minute_inr=round(item["price_per_minute"] * usd_to_inr_rate, 4),
        )
        for item in (response.data or {}).get("llm_prices", [])
    ]

    return LlmPricingResponse(llm_prices=llm_prices, usd_to_inr_rate=usd_to_inr_rate)


# -------------------- GET BY ID --------------------


@router.get(
    "by-id/{agent_id}",
    response_model=AgentRead,
    summary="Get agent by ID",
)
async def get_agent_by_id(
    agent_id: int,
):
    agent = (
        db.session.query(AgentModel)
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice),
            selectinload(AgentModel.phone_number),
            selectinload(AgentModel.variables),
            selectinload(AgentModel.agent_knowledge_bases),
            selectinload(AgentModel.agent_functions)
        )
        .filter(
            AgentModel.id == agent_id,
        )
        .first()
    )

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    prune_stale_agent_transfers(agent, db.session)

    return agent_to_read(agent)


# -------------------- TOOL ATTACH / DETACH --------------------
# Scoped strictly to the (agent, tool) link — never touches the shared
# FunctionModel row itself, so detaching a tool here can never affect any
# other agent that also has it attached.

def _sync_agent_tool_ids_with_elevenlabs(agent: AgentModel) -> None:
    """Rebuilds the agent's full tool_ids list from bridge rows and pushes it
    to ElevenLabs. Raises HTTPException(424) on failure — caller must rollback."""
    if not agent.elevenlabs_agent_id:
        return

    bound_tool_ids = [
        row.function_id
        for row in db.session.query(AgentFunctionBridgeModel)
        .filter(AgentFunctionBridgeModel.agent_id == agent.id)
        .all()
    ]
    el_tool_ids = []
    if bound_tool_ids:
        tools = db.session.query(FunctionModel).filter(
            FunctionModel.id.in_(bound_tool_ids),
            FunctionModel.elevenlabs_tool_id.isnot(None),
        ).all()
        el_tool_ids = [t.elevenlabs_tool_id for t in tools]

    el_client = ElevenLabsAgent()
    el_response = el_client.update_agent(agent_id=agent.elevenlabs_agent_id, tool_ids=el_tool_ids)
    if not el_response.status:
        logger.error(f"❌ ElevenLabs agent tool resync failed: {el_response.error_message}")
        raise HTTPException(
            status_code=424,
            detail=f"Failed to sync tools: {el_response.error_message}",
        )


@router.post(
    "/{agent_id}/tools/{function_id}",
    response_model=AgentRead,
    summary="Attach an existing tool to this agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def attach_tool_to_agent(
    agent_id: int,
    function_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    agent = db.session.query(AgentModel).filter(
        AgentModel.id == agent_id,
        AgentModel.user_id == current_user.id,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    tool = db.session.query(FunctionModel).filter(
        FunctionModel.id == function_id,
        FunctionModel.elevenlabs_tool_id.isnot(None),
        or_(
            FunctionModel.user_id == current_user.id,
            FunctionModel.user_id.is_(None),
        ),
    ).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found or not accessible")

    existing_bridge = db.session.query(AgentFunctionBridgeModel).filter(
        AgentFunctionBridgeModel.agent_id == agent_id,
        AgentFunctionBridgeModel.function_id == function_id,
    ).first()
    if not existing_bridge:
        db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=function_id))
        db.session.flush()

    try:
        _sync_agent_tool_ids_with_elevenlabs(agent)
    except HTTPException:
        db.session.rollback()
        raise

    db.session.commit()
    db.session.refresh(agent)
    return agent_to_read(agent)


@router.delete(
    "/{agent_id}/tools/{function_id}",
    response_model=AgentRead,
    summary="Detach a tool from this agent (does not delete the tool itself)",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def detach_tool_from_agent(
    agent_id: int,
    function_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    agent = db.session.query(AgentModel).filter(
        AgentModel.id == agent_id,
        AgentModel.user_id == current_user.id,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    tool = db.session.query(FunctionModel).filter(FunctionModel.id == function_id).first()
    if tool and tool.is_system_managed:
        raise HTTPException(status_code=403, detail="This tool is managed automatically and cannot be detached.")

    db.session.query(AgentFunctionBridgeModel).filter(
        AgentFunctionBridgeModel.agent_id == agent_id,
        AgentFunctionBridgeModel.function_id == function_id,
    ).delete()
    db.session.flush()

    try:
        _sync_agent_tool_ids_with_elevenlabs(agent)
    except HTTPException:
        db.session.rollback()
        raise

    db.session.commit()
    db.session.refresh(agent)
    return agent_to_read(agent)


# -------------------- UPDATE --------------------

@router.put(
    "/{agent_id}",
    response_model=AgentRead,
    summary="Update agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def update_agent(
    agent_id: int,
    agent_in: AgentUpdate,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    agent = (
        db.session.query(AgentModel)
        .options(
            selectinload(AgentModel.agent_ai_models).selectinload(AgentAIModelBridge.ai_model),
            selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language),
            selectinload(AgentModel.voice),
            selectinload(AgentModel.phone_number)
        )
        .filter(
            AgentModel.id == agent_id,
            AgentModel.user_id == current_user.id,
        )
        .first()
    )

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ---- ElevenLabs Synchronization Preparation ----
    el_update_params = {}
    
    # ---- Phone Number Update ----
    if agent_in.phone is not None:
        old_phone = db.session.query(PhoneNumberService).filter(
            PhoneNumberService.assigned_to == agent_id
        ).first()

        new_phone_value = agent_in.phone.strip() if agent_in.phone else ""

        if not (old_phone and old_phone.phone_number == new_phone_value):
            # Actual change (or explicit unassign via empty string) — swap it over.
            unassign_phone(old_phone)

            new_phone_record, new_phone_connector = resolve_phone_record(
                db.session, current_user.id, agent_in.phone, agent_in.twilio_connector_id, current_agent_id=agent_id
            )
            finalize_phone_assignment(new_phone_record, agent, new_phone_connector)
    
    # ---- Base Fields ----
    if agent_in.agent_name is not None:
        # Same uniqueness rule as create: no OTHER agent of this user may share
        # the name (case-insensitive).
        name_taken = (
            db.session.query(AgentModel)
            .filter(
                func.lower(AgentModel.agent_name) == agent_in.agent_name.lower(),
                AgentModel.user_id == current_user.id,
                AgentModel.id != agent_id,
            )
            .first()
        )
        if name_taken:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent with this name already exists",
            )
        agent.agent_name = agent_in.agent_name
        el_update_params["name"] = agent_in.agent_name
    if agent_in.first_message is not None:
        agent.first_message = agent_in.first_message
        el_update_params["first_message"] = agent_in.first_message
    if agent_in.system_prompt is not None:
        # Re-apply (or keep absent) the personal-KB tool prompt block based
        # on this agent's actual current tool state, independent of whether
        # the client's submitted prompt happens to contain one — the client
        # never sees the block (see agent_to_read), so it can't be trusted
        # to round-trip it correctly on its own.
        new_prompt = apply_prompt_block_state(agent.id, agent_in.system_prompt)
        agent.system_prompt = new_prompt
        el_update_params["prompt"] = new_prompt
    if agent_in.is_enabled is not None:
        if agent_in.is_enabled == True and agent.is_enabled == False:
            check_can_enable_resource(current_user.id, "ai_voice_agents", allow_coin_fallback=True)
            db.session.query(WidgetModel).filter(
                WidgetModel.agent_id == agent.id
            ).update({WidgetModel.is_enabled: True})
            db.session.query(WebAgentPageModel).filter(
                WebAgentPageModel.agent_id == agent.id
            ).update({WebAgentPageModel.is_enabled: True})
        if agent_in.is_enabled == False and agent.is_enabled == True:
            db.session.query(WidgetModel).filter(
                WidgetModel.agent_id == agent.id
            ).update({WidgetModel.is_enabled: False})
            db.session.query(WebAgentPageModel).filter(
                WebAgentPageModel.agent_id == agent.id
            ).update({WebAgentPageModel.is_enabled: False})
        agent.is_enabled = agent_in.is_enabled
    if agent_in.timezone is not None:
        agent.timezone = agent_in.timezone
        el_update_params["timezone"] = agent_in.timezone

    # ---- Voice ----
    if agent_in.voice is not None:
        voice = (
            db.session.query(VoiceModel)
            .filter(
                VoiceModel.voice_name == agent_in.voice,
                VoiceModel.elevenlabs_voice_id.isnot(None),
                or_(
                    VoiceModel.user_id == current_user.id,
                    VoiceModel.user_id.is_(None),
                ),
            )
            .first()
        )
        if not voice:
            raise HTTPException(
                status_code=400,
                detail=f"Voice '{agent_in.voice}' not found or not synced. Run the voice sync script, then use a voice from the list.",
            )
        if voice.is_enabled == False:
            raise HTTPException(
                status_code=400,
                detail=f"Voice '{agent_in.voice}' is disabled",
            )
        agent.agent_voice = voice.id
        el_update_params["voice_id"] = voice.elevenlabs_voice_id

    # ---- AI Model ----
    if agent_in.ai_model is not None:
        db.session.query(AgentAIModelBridge).filter(
            AgentAIModelBridge.agent_id == agent_id
        ).delete()

        ai_model = (
            db.session.query(AIModels)
            .filter(AIModels.model_name == agent_in.ai_model)
            .first()
        )

        if not ai_model:
            raise HTTPException(status_code=400, detail="Invalid AI model")

        db.session.add(
            AgentAIModelBridge(
                agent_id=agent_id,
                ai_model_id=ai_model.id,
            )
        )
        el_update_params["llm_model"] = ai_model.model_name

    # ---- Language ----
    if agent_in.language is not None:
        db.session.query(AgentLanguageBridge).filter(
            AgentLanguageBridge.agent_id == agent_id
        ).delete()

        language = (
            db.session.query(LanguageModel)
            .filter(LanguageModel.lang_code == agent_in.language)
            .first()
        )

        if not language:
            raise HTTPException(status_code=400, detail="Invalid language code")

        db.session.add(
            AgentLanguageBridge(
                agent_id=agent.id,
                lang_id=language.id,
            )
        )
        el_update_params["language"] = language.lang_code

    # ---- Knowledge Base Update ----
    if agent_in.knowledgebase is not None:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
        kb_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order
        
        # 2. Fetch from DB
        kb_records = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id.in_(kb_ids_ordered),
            KnowledgeBaseModel.user_id == current_user.id,
            KnowledgeBaseModel.elevenlabs_document_id.isnot(None)
        ).all()
        
        # 3. Create a map for O(1) lookup
        kb_map = {kb.id: kb for kb in kb_records}

        # 4. Validate all IDs exist checking against the unique set of requested IDs
        found_ids = set(kb_map.keys())
        missing_ids = set(kb_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Knowledge Base IDs not found or not synced: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        el_kb_list = []
        for kb_id in kb_ids_ordered:
            kb = kb_map[kb_id]
            el_kb_list.append({
                "id": kb.elevenlabs_document_id,
                "type": "file",
                "name": kb.title or f"KB_{kb.id}"
            })
        
        el_update_params["knowledge_base"] = el_kb_list

        # Update DB bridge (delete old, add new)
        db.session.query(AgentKnowledgeBaseBridge).filter(
            AgentKnowledgeBaseBridge.agent_id == agent_id
        ).delete()
        for kb_id in kb_ids_ordered:
            db.session.add(AgentKnowledgeBaseBridge(agent_id=agent_id, kb_id=kb_id))

    # ---- Tools Update ----
    if agent_in.tools is not None:
        # 1. Extract IDs and deduplicate while preserving order
        raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
        tool_ids_ordered = list(dict.fromkeys(raw_ids)) # Deduplicate preserving order

        # 2. Fetch from DB
        tool_records = db.session.query(FunctionModel).filter(
            FunctionModel.id.in_(tool_ids_ordered),
            FunctionModel.elevenlabs_tool_id.isnot(None),
            or_(
                FunctionModel.user_id == current_user.id,
                FunctionModel.user_id.is_(None)
            )
        ).all()
        
        # 3. Create a map for O(1) lookup
        tool_map = {tool.id: tool for tool in tool_records}

        # 4. Validate all IDs exist
        found_ids = set(tool_map.keys())
        missing_ids = set(tool_ids_ordered) - found_ids
        
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Some Tool IDs not found or not synced or not accessible to you: {list(missing_ids)}"
            )
        
        # 5. Construct ElevenLabs list in the original order using the map
        el_tool_ids = []
        for tool_id in tool_ids_ordered:
            tool = tool_map[tool_id]
            el_tool_ids.append(tool.elevenlabs_tool_id)

        el_update_params["tool_ids"] = el_tool_ids

        # Update DB bridge
        db.session.query(AgentFunctionBridgeModel).filter(
            AgentFunctionBridgeModel.agent_id == agent_id
        ).delete()
        for tool_id in tool_ids_ordered:
            db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=tool_id))

    # ---- Variables Update ----
    # agent.system_prompt was already updated above (if provided), so this
    # reflects whichever prompt will be live after this request. The prompt
    # is the source of truth for which variables exist: whatever
    # {{placeholder}} names it contains is exactly the variable set that
    # survives — anything else (even if explicitly re-sent in the payload,
    # e.g. because the frontend echoed back a stale value it fetched
    # earlier) gets dropped. Explicit values in agent_in.variables still win
    # for placeholders that ARE present; existing DB values are the fallback
    # for ones untouched by this request.
    if agent_in.variables is not None or agent_in.system_prompt is not None:
        prompt_var_names = extract_prompt_variable_names(agent.system_prompt)
        existing_variables = {v.variable_name: v.variable_value for v in agent.variables}
        explicit_variables = agent_in.variables or {}

        synced_variables = {
            name: explicit_variables.get(name, existing_variables.get(name, ""))
            for name in prompt_var_names
        }

        if synced_variables != existing_variables:
            db.session.query(VariablesModel).filter(
                VariablesModel.agent_id == agent_id
            ).delete()
            for key, value in synced_variables.items():
                db.session.add(VariablesModel(agent_id=agent_id, variable_name=key, variable_value=value))
            el_update_params["dynamic_variables"] = synced_variables

    # ---- Builtin Tools Update ----
    if agent_in.built_in_tools is not None:
        # transform_built_in_tools may drop invalid transfers (e.g. an agent_id
        # that no longer resolves) from agent_in.built_in_tools in place, so run
        # it before the model_dump() to keep the persisted config in sync with
        # what was actually sent to ElevenLabs.
        el_update_params["built_in_tools"] = transform_built_in_tools(agent_in.built_in_tools, db.session, current_user.id, current_agent_id=agent_id)
        agent.built_in_tools = agent_in.built_in_tools.model_dump()

    # agent.system_prompt/agent.timezone already reflect this request's changes
    # (if any) from the blocks above, so this check covers the effective state.
    if prompt_requires_timezone(agent.system_prompt) and not agent.timezone:
        raise HTTPException(
            status_code=400,
            detail="timezone is required when the system prompt uses {{system__time}}, {{system__time_utc}}, or {{system__timezone}}"
        )

    # ---- Sync with ElevenLabs ----
    if el_update_params and agent.elevenlabs_agent_id:
        try:
            logger.info(f"Updating agent '{agent.elevenlabs_agent_id}' in ElevenLabs")
            el_client = ElevenLabsAgent()
            el_response = el_client.update_agent(
                agent_id=agent.elevenlabs_agent_id,
                **el_update_params
            )
            
            if not el_response.status:
                logger.error(f"❌ ElevenLabs agent update failed: {el_response.error_message}")
                db.session.rollback()
                raise HTTPException(
                    status_code=424,
                    detail=f"Failed to update agent: {el_response.error_message}"
                )

            logger.info(f"✅ ElevenLabs agent '{agent.elevenlabs_agent_id}' updated successfully")

            # Refresh the stored per-minute LLM price estimate now that
            # ElevenLabs has the updated config (prompt / KB / RAG / model all
            # affect it). Best-effort — never block the update on this.
            effective_model = el_update_params.get("llm_model")
            if not effective_model:
                effective_model = (
                    db.session.query(AIModels.model_name)
                    .join(AgentAIModelBridge, AgentAIModelBridge.ai_model_id == AIModels.id)
                    .filter(AgentAIModelBridge.agent_id == agent_id)
                    .scalar()
                )
            if effective_model:
                agent.llm_price_per_minute = el_client.get_llm_price_per_minute(
                    agent.elevenlabs_agent_id, effective_model
                )

            # Refresh LLM-cost calibration constants too (best-effort). Query
            # current KB bridge rows directly rather than relying on
            # el_kb_list, which is only set when this request touched KB.
            agent.rag_enabled = True
            has_kb = db.session.query(AgentKnowledgeBaseBridge.id).filter(
                AgentKnowledgeBaseBridge.agent_id == agent_id
            ).first() is not None
            agent.kb_total_pages = (
                ElevenLabsKB().get_kb_total_pages(agent.elevenlabs_agent_id) if has_kb else 0
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during ElevenLabs agent update: {e}")
            db.session.rollback()
            raise HTTPException(
                status_code=424,
                detail=f"Failed to update agent due to an unexpected error: {str(e)}"
            )

    db.session.commit()
    db.session.refresh(agent)

    log_activity(
        user_id=current_user.id,
        event_type="agent_updated",
        description=f"Updated agent: {agent.agent_name}",
        metadata={"agent_id": agent.id, "elevenlabs_agent_id": agent.elevenlabs_agent_id}
    )

    # Best-effort: this update may have replaced the agent's whole tool_ids
    # list (via agent_in.functions) without knowing about its personal KB
    # search tool (if it has one). Re-push it so it isn't silently dropped —
    # must never fail the update itself.
    try:
        resync_personal_kb_tool_for_agent(agent.id)
    except Exception as e:
        logger.warning(f"Failed to re-sync personal KB tool onto agent {agent.id} after update: {e}")

    return agent_to_read(agent)


# -------------------- DELETE --------------------

@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete agent",
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def delete_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
    agent = (
        db.session.query(AgentModel)
        .filter(
            AgentModel.id == agent_id,
            AgentModel.user_id == current_user.id,
        )
        .first()
    )

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ---- Unassign phone number first ----
    assigned_phone = db.session.query(PhoneNumberService).filter(
        PhoneNumberService.assigned_to == agent_id
    ).first()

    unassign_phone(assigned_phone)
    db.session.flush()  # Use flush instead of commit to allow rollback if ElevenLabs fails

    # ---- Delete from ElevenLabs ----
    if agent.elevenlabs_agent_id:
        try:
            logger.info(f"Deleting agent from ElevenLabs: {agent.elevenlabs_agent_id}")
            el_client = ElevenLabsAgent()
            el_response = el_client.delete_agent(agent.elevenlabs_agent_id)
            
            if el_response.status:
                logger.info(f"✅ Agent deleted from ElevenLabs: {agent.elevenlabs_agent_id}")
            else:
                logger.warning(f"Failed to delete agent from ElevenLabs: {el_response.error_message}")
        except Exception as e:
            logger.error(f"Error deleting agent from ElevenLabs: {e}")

    # Must run before the agent row is deleted — it looks up this agent's
    # dedicated personal KB tool via its (about to cascade-delete) bridge row.
    try:
        delete_agent_personal_kb_tool(agent_id)
    except Exception as e:
        logger.warning(f"Failed to clean up personal KB tool for deleted agent {agent_id}: {e}")

    db.session.delete(agent)
    db.session.commit()

@router.post(
    "/config",
    response_model=AgentConfigOut,
    status_code=status.HTTP_200_OK,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def generate_system_prompt_for_agent(
    agent_config: AgentConfigGenerator,
    current_user: UnifiedAuthModel = Depends(require_active_user()),
):
        try:
            agent_exists = (
                db.session.query(AgentModel).filter(
                    func.lower(AgentModel.agent_name) == agent_config.agent_name.lower(),
                    AgentModel.user_id == current_user.id
                ).first()
            )

            if agent_exists:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Agent with this name already exists"
                )

            system_prompt =  await generate_system_prompt_async(agent_config)
            
            if not system_prompt:
                logger.error("failed to generate system prompt")
                raise HTTPException(
                    status_code= status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="could not generate system prompt at the moment"
                )
            
            response_config = AgentConfigOut(
                agent_name=agent_config.agent_name,
                ai_model=agent_config.ai_model,
                voice=agent_config.voice,
                language=agent_config.language,
                system_prompt=system_prompt,
            )
            logger.info("system prompt generated successfully")

            return response_config
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"error while genreating system prompt {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to generate system prompt at the moment: {str(e)}"
            )
