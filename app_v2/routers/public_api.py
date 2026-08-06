# ... (Keep previous imports and helpers)
# I'll just rewrite and expand the whole file content to be sure.
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session, selectinload
from fastapi_sqlalchemy import db
from sqlalchemy import or_
from typing import List, Optional, Literal
import json
import math
import uuid
import os
import shutil
from datetime import datetime, timezone

from app_v2.databases.models import (
    AgentModel,
    WidgetModel,
    WebAgentPageModel,
    UnifiedAuthModel,
    VoiceModel,
    AIModels,
    LanguageModel,
    KnowledgeBaseModel,
    PersonalKnowledgeBaseModel,
    PersonalKnowledgeBaseChunkModel,
    PersonalKnowledgeBaseAgentBridgeModel,
    FunctionModel,
    VariablesModel,
    AgentAIModelBridge,
    AgentLanguageBridge,
    AgentKnowledgeBaseBridge,
    AgentLanguageBridge,
    AgentKnowledgeBaseBridge,
    AgentFunctionBridgeModel,
    FunctionApiConfig,
    TwilioUserCreds,
    ConversationsModel,
    WidgetLeadModel,
    VoiceTraitsModel,
    CoinUsageSettingsModel,
)
from app_v2.utils.conversation_lifecycle import is_agents_first_call
from app_v2.schemas.function_schema import (
    FunctionCreateSchema,
    FunctionUpdateSchema,
    FunctionRead,
    ApiSchema,
    FunctionBind,
    FunctionUnbind,
    PrimitiveField
)
from app_v2.schemas.agent_schema import (
    PublicAgentCreate,
    PublicAgentUpdate,
    PublicAgentRead,
    PublicAgentListRead,
)
from app_v2.schemas.built_in_tools import BuiltInToolsParams, TransferToAgentConfig, PublicBuiltInToolsParams
from app_v2.schemas.widget_schema import WidgetConfigResponse, WidgetListResponse, PublicWidgetConfig, PublicWidgetConfigUpdate
from app_v2.schemas.web_agent_schema import WebAgentCreate, WebAgentUpdate, WebAgentResponse, WebAgentListResponse
from app_v2.schemas.twilio_connector_schema import TwilioConnectorCreate, TwilioConnectorUpdate, TwilioConnectorResponse
from app_v2.schemas.language_schema import LanguageRead
from app_v2.schemas.voice_schema import VoiceRead, PublicVoiceListRead, PublicVoiceRead
from app_v2.schemas.ai_model import AIModelRead
from app_v2.schemas.knowledge_base_schema import (
    KnowledgeBaseResponse,
    KnowledgeBaseURLCreate,
    KnowledgeBaseTextCreate,
    KnowledgeBaseBind
)
from app_v2.schemas.personal_knowledge_base_schema import (
    PersonalKnowledgeBaseResponse,
    PersonalKnowledgeBaseURLCreate,
    PersonalKnowledgeBaseTextCreate,
    PersonalKnowledgeBaseURLUpdate,
    PersonalKnowledgeBaseTextUpdate,
)
from app_v2.schemas.pagination import PublicPaginatedResponse
from app_v2.schemas.enum_types import PhoneNumberAssignStatus, GenderEnum, RequestMethodEnum, PlanFeatureEnum, PublicLogChannelEnum
from app_v2.utils.public_auth import get_public_api_user, require_json_accept
from app_v2.utils.crypto_utils import encrypt_data, decrypt_data
from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException
from app_v2.utils.rate_limit import track_and_limit_api, log_public_api_call
from app_v2.utils.log_sanitizer import sanitize_for_log
from app_v2.utils.feature_access import RequireFeaturePublic, require_feature_enabled, check_can_enable_resource
from app_v2.utils.elevenlabs.agent_utils import ElevenLabsAgent, describe_agent_sync_error
from app_v2.utils.elevenlabs import ElevenLabsKB, describe_kb_sync_error
from app_v2.utils.scraping_utils import scrape_webpage_title
from app_v2.utils.activity_logger import log_activity
from app_v2.utils.text_extraction import extract_text_from_file
from app_v2.utils.web_scraper import scrape_url
from app_v2.utils.faiss_store import remove_embeddings
from app_v2.utils.personal_kb_tool import (
    ensure_personal_kb_tool_for_agent,
    remove_personal_kb_tool_from_agent_if_empty,
    delete_agent_personal_kb_tool,
    strip_prompt_block,
    apply_prompt_block_state,
)
from app_v2.routers.personal_knowledge_base import (
    _store_kb_entry as _store_personal_kb_entry,
    _kb_to_read as _personal_kb_to_read,
    _replace_kb_content as _replace_personal_kb_content,
    UPLOAD_DIR as PERSONAL_KB_UPLOAD_DIR,
    ALLOWED_EXTENSIONS as PERSONAL_KB_ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE_IN_MB as PERSONAL_KB_MAX_FILE_SIZE_IN_MB,
)
from app_v2.core.logger import setup_logger
from fastapi import UploadFile, File, Form
import time
from app_v2.schemas.api_analytics_schema import APIAnalyticsResponse, APICallLogRead
from sqlalchemy import func
from datetime import timedelta
from app_v2.core.elevenlabs_config import CUSTOM_LLM_MODEL_NAME

logger = setup_logger(__name__)

from fastapi.routing import APIRoute
from typing import Callable
from fastapi.responses import Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from app_v2.core.exceptions import get_readable_message

def _try_parse_json_bytes(raw: bytes):
    """Best-effort JSON parse; returns None on empty/invalid/non-JSON bytes."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _public_envelope(status_str: str, data=None, message: str = "", detail: str = "") -> dict:
    return {"status": status_str, "data": data, "message": message, "detail": detail}


def _find_duplicate_json_keys(raw: bytes) -> Optional[List[str]]:
    """Detects repeated keys in a JSON object (at any nesting level, e.g.
    `variables`). By default `json.loads` silently keeps only the last value
    for a repeated key, discarding exactly the information needed to flag it
    as a mistake — so this re-parses with an `object_pairs_hook` that
    remembers keys seen more than once instead of collapsing them."""
    if not raw:
        return None
    duplicates = []

    def _check_pairs(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen and k not in duplicates:
                duplicates.append(k)
            seen.add(k)
        return dict(pairs)

    try:
        json.loads(raw, object_pairs_hook=_check_pairs)
    except (ValueError, TypeError):
        return None  # Not valid JSON at all — let the normal request flow report that.
    return duplicates or None


class PublicAPIRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()
        route_path = self.path  # path TEMPLATE (e.g. "/api/v2/public/agents/{agent_id}"), so logs group by endpoint
        async def custom(request: Request) -> Response:
            start_time = time.time()
            status_code = 500
            error_message = None
            response = None
            raw_request_body = b""
            try:
                # Cached by Starlette, so reading it here doesn't consume the
                # handler's own await request.json()/request.body() calls.
                # Read inside the try so a failure here (e.g. client
                # disconnect mid-body) still comes back enveloped instead of
                # bypassing _public_envelope entirely.
                raw_request_body = await request.body()
                duplicate_keys = _find_duplicate_json_keys(raw_request_body)
                if duplicate_keys:
                    status_code = 400
                    error_message = (
                        "Your request contains a repeated field name. Please remove the "
                        "duplicate and try again."
                    )
                    detail_message = (
                        f"Duplicate key(s) found in request body: {', '.join(duplicate_keys)}. "
                        "Each key must appear only once."
                    )
                    response = JSONResponse(
                        status_code=status_code,
                        content=_public_envelope("failed", message=error_message, detail=detail_message),
                    )
                    return response
                response = await original(request)
                status_code = response.status_code
                payload = _try_parse_json_bytes(getattr(response, "body", None))
                # A handler can set request.state.public_message (and,
                # optionally, a distinct request.state.public_detail) to
                # surface extra context on an otherwise-plain success
                # response — e.g. update_agent_public reporting how many
                # widgets/web agents got cascaded-disabled along with the
                # agent. Empty/unset for every other endpoint.
                success_message = getattr(request.state, "public_message", "")
                success_detail = getattr(request.state, "public_detail", success_message)
                response = JSONResponse(
                    status_code=status_code,
                    content=_public_envelope("success", data=payload, message=success_message, detail=success_detail),
                )
                return response
            except HTTPException as e:
                status_code = e.status_code
                # `detail` is usually a plain string shared by both `message`
                # and `detail` in the envelope, as it always has been - but a
                # handful of raises (e.g. the transfer_to_agent duplicate-
                # condition check in agents.py's transform_built_in_tools)
                # pass a {"message": ..., "detail": ...} dict instead, same
                # shape main.py's global HTTPException handler already
                # supports for the internal (non-public) API.
                if isinstance(e.detail, dict):
                    error_message = e.detail.get("message", "Something went wrong")
                    detail_message = e.detail.get("detail", error_message)
                else:
                    error_message = str(e.detail)
                    detail_message = error_message
                response = JSONResponse(
                    status_code=status_code,
                    content=_public_envelope("failed", message=error_message, detail=detail_message),
                )
                return response
            except RequestValidationError as e:
                status_code = 400
                field_errors = []
                # `detail` is the dev-facing counterpart of `message` (see
                # `_public_envelope` docstring context above / HTTPException
                # and generic-Exception branches below, which follow the
                # same split) — it carries the raw loc/type/input pydantic
                # gives us, so `message` can stay in plain, user-facing
                # language without exposing Python-ish internals.
                detail_errors = []
                for err in e.errors():
                    loc = err.get("loc", [])
                    field = loc[-1] if loc else "field"
                    raw_msg = err.get("msg", "Invalid value")
                    field_errors.append(get_readable_message(field, raw_msg))
                    loc_path = ".".join(str(part) for part in loc) if loc else str(field)
                    detail_errors.append(f"{loc_path} ({err.get('type', 'unknown')}): {raw_msg}")
                error_message = "; ".join(field_errors)
                detail_message = "; ".join(detail_errors)
                response = JSONResponse(
                    status_code=status_code,
                    content=_public_envelope("failed", message=error_message, detail=detail_message),
                )
                return response
            except Exception as e:
                status_code = 500
                error_message = str(e)
                logger.error(f"Unhandled error in public API {route_path}: {e}", exc_info=True)
                response = JSONResponse(
                    status_code=status_code,
                    content=_public_envelope(
                        "failed",
                        message="Something went wrong. Please try again later.",
                        detail=error_message,
                    ),
                )
                return response
            finally:
                process_time_ms = int((time.time() - start_time) * 1000)
                client_id = request.headers.get("X-API-Client-ID")
                if client_id:
                    try:
                        with db():
                            from app_v2.databases.models import APIKeyModel
                            # Not filtering on is_active here: this is a diagnostic
                            # lookup to attribute the log to its owning user, not an
                            # authorization check (that already happened/failed in
                            # get_public_api_user). Filtering on is_active meant a
                            # request made with a deactivated key never got logged
                            # at all, even though we know exactly whose key it was.
                            key = db.session.query(APIKeyModel).filter_by(client_id=client_id).first()
                            # `key` can be None here (client_id didn't match any API
                            # key at all — typo'd/garbage/deleted key). We still want
                            # 3xx/4xx/5xx to be logged rather than silently dropped,
                            # so we fall back to an unattributed log row instead of
                            # requiring a resolvable user_id.
                            content_type = request.headers.get("content-type", "")
                            if "application/json" in content_type:
                                req_body_parsed = _try_parse_json_bytes(raw_request_body)
                            elif raw_request_body:
                                req_body_parsed = {"_unloggable": True, "content_type": content_type}
                            else:
                                req_body_parsed = None
                            resp_body_parsed = (
                                _try_parse_json_bytes(getattr(response, "body", None))
                                if response is not None else None
                            )
                            log_public_api_call(
                                user_id=key.user_id if key else None,
                                api_route=route_path,
                                status_code=status_code,
                                response_time_ms=process_time_ms,
                                coins_used=0,
                                channel=PublicLogChannelEnum.public_api,
                                method=request.method,
                                request_params={
                                    "path_params": dict(request.path_params),
                                    "query_params": dict(request.query_params),
                                },
                                request_body=sanitize_for_log(req_body_parsed),
                                response_body=sanitize_for_log(resp_body_parsed),
                                is_success=(200 <= status_code < 300),
                                error_message=error_message,
                                api_key_id=key.id if key else None,
                                attempted_client_id=None if key else client_id,
                            )
                    except Exception as e:
                        logger.error(f"Failed to log public API call in route handler: {e}")

        return custom

router = APIRouter(
    prefix="/api/v2/public",
    tags=["public-api"],
    dependencies=[Depends(require_json_accept), Depends(get_public_api_user)],
    route_class=PublicAPIRoute
)

# -------------------- HELPERS --------------------

# Public callers can only configure/see end_call and transfer_to_agent (see
# PublicBuiltInToolsParams) — both are simpler shapes than their internal
# equivalent (end_call is a plain bool instead of {enabled, name};
# transfer_to_agent is a flat list instead of {enabled, name, transfers}).
# These two helpers convert between that public shape and the internal
# BuiltInToolsParams shape actually stored on the agent / passed to
# transform_built_in_tools (shared with the internal router).

def to_internal_built_in_tools(public_params: Optional[PublicBuiltInToolsParams]) -> Optional[BuiltInToolsParams]:
    if not public_params:
        return None

    transfer_to_agent_config = None
    if public_params.transfer_to_agent is not None:
        transfer_to_agent_config = TransferToAgentConfig(
            enabled=bool(public_params.transfer_to_agent),
            transfers=public_params.transfer_to_agent,
        )

    return BuiltInToolsParams(
        end_call=public_params.end_call,
        transfer_to_agent=transfer_to_agent_config,
    )


def _public_built_in_tools(built_in_tools: Optional[dict]) -> Optional[dict]:
    """Renders a stored (internal-shaped) built_in_tools dict back into the
    simplified public shape: end_call as a plain bool, transfer_to_agent as
    a flat list of {agent_id, condition} — dropping transfer_to_number and
    play_keypad_touch_tone entirely, which the public API never exposes."""
    if not built_in_tools:
        return built_in_tools

    end_call = built_in_tools.get("end_call")
    end_call_enabled = bool(end_call.get("enabled")) if isinstance(end_call, dict) else bool(end_call)

    transfer_to_agent = built_in_tools.get("transfer_to_agent")
    transfers = (transfer_to_agent or {}).get("transfers") or [] if isinstance(transfer_to_agent, dict) else []

    return {
        "end_call": end_call_enabled,
        "transfer_to_agent": transfers,
    }


def agent_to_read(agent: AgentModel) -> PublicAgentRead:
    ai_model = agent.agent_ai_models[0].ai_model if agent.agent_ai_models else None
    language = agent.agent_languages[0].language if agent.agent_languages else None

    is_first_call_pending = is_agents_first_call(agent.id)
    first_call_max_duration_seconds = (
        CoinUsageSettingsModel.get_settings().first_call_max_duration_seconds
        if is_first_call_pending else None
    )

    return PublicAgentRead(
        id=agent.id,
        agent_name=agent.agent_name,
        first_message=agent.first_message,
        # The personal-KB tool prompt block is an implementation detail the
        # user never typed and shouldn't see/edit — hidden here, reapplied
        # on write via apply_prompt_block_state() if the agent still has an
        # active tool (see update_agent_public below).
        system_prompt=strip_prompt_block(agent.system_prompt),
        voice=agent.voice.id,
        voice_name=agent.voice.voice_name,
        is_enabled=agent.is_enabled,
        ai_model=ai_model.id if ai_model else None,
        ai_model_name=ai_model.model_name if ai_model else None,
        language=language.id if language else None,
        language_name=language.language if language else None,
        created_at=agent.created_at,
        updated_at=agent.modified_at,
        # Personal KB (PersonalKnowledgeBaseAgentBridgeModel) — see create_agent
        # for why this API uses personal KB, not the legacy KnowledgeBaseModel.
        knowledgebase = [
            {"id": bridge.knowledge_base.id, "title": bridge.knowledge_base.title, "type": bridge.knowledge_base.kb_type}
            for bridge in agent.personal_kb_agent_bridges
        ],
        variables={var.variable_name: var.variable_value for var in agent.variables},
        tools=[
            {"id": bridge.function.id, "name": bridge.function.name}
            for bridge in agent.agent_functions
        ],
        built_in_tools=_public_built_in_tools(agent.built_in_tools),
        timezone=agent.timezone,
        is_first_call_pending=is_first_call_pending,
        first_call_max_duration_seconds=first_call_max_duration_seconds,
    )


def agent_to_list_read(
    agent: AgentModel,
    kb_count: int = 0,
    tool_count: int = 0,
    conversation_count: int = 0,
    leads_count: int = 0,
    is_first_call_pending: bool = True,
) -> PublicAgentListRead:
    ai_model = agent.agent_ai_models[0].ai_model if agent.agent_ai_models else None
    language = agent.agent_languages[0].language if agent.agent_languages else None

    return PublicAgentListRead(
        id=agent.id,
        agent_name=agent.agent_name,
        first_message=agent.first_message,
        voice=agent.voice.id,
        voice_name=agent.voice.voice_name,
        is_enabled=agent.is_enabled,
        ai_model=ai_model.id if ai_model else None,
        ai_model_name=ai_model.model_name if ai_model else None,
        language=language.id if language else None,
        language_name=language.language if language else None,
        created_at=agent.created_at,
        updated_at=agent.modified_at,
        timezone=agent.timezone,
        kb_count=kb_count,
        tool_count=tool_count,
        conversation_count=conversation_count,
        leads_count=leads_count,
        is_first_call_pending=is_first_call_pending,
    )

def widget_to_response(widget: WidgetModel, request: Request = None) -> WidgetConfigResponse:
    base_url = str(request.base_url).rstrip("/") if request else ""
    return WidgetConfigResponse(
        id=widget.id,
        public_id=widget.public_id,
        widget_name=widget.widget_name,
        shareable_link=f"{base_url}/api/v2/widget/preview/{widget.public_id}",
        agent_id=widget.agent_id,
        is_enabled=widget.is_enabled,
        appearance={
            "widget_title": widget.widget_title,
            "widget_subtitle": widget.widget_subtitle,
            "primary_color": widget.primary_color,
            "position": widget.position,
            "show_branding": widget.show_branding
        },
        prechat={
            "enable_prechat": widget.enable_prechat,
            "require_name": widget.require_name,
            "require_email": widget.require_email,
            "require_phone": widget.require_phone,
            "custom_fields": widget.custom_fields or []
        }
    )

def twilio_connector_to_response(connector: TwilioUserCreds) -> TwilioConnectorResponse:
    return TwilioConnectorResponse(
        id=connector.id,
        name=connector.name,
        account_sid=connector.account_sid,
        auth_token=decrypt_data(connector.auth_token),
        created_at=connector.created_at,
    )

def _voice_traits(voice: VoiceModel):
    gender = GenderEnum.male
    nationality = "british"
    if voice.traits:
        gender = voice.traits.gender.value if hasattr(voice.traits.gender, 'value') else str(voice.traits.gender)
        nationality = voice.traits.nationality
    return gender, nationality


def voice_to_list_read(voice: VoiceModel) -> PublicVoiceListRead:
    gender, nationality = _voice_traits(voice)
    return PublicVoiceListRead(
        id=voice.id,
        voice_name=voice.voice_name,
        is_custom_voice=voice.is_custom_voice,
        gender=gender,
        nationality=nationality,
        has_sample_audio=voice.has_sample_audio,
        sample_audio_url=voice.audio_file,
        is_enabled=voice.is_enabled,
    )


def voice_to_read(voice: VoiceModel) -> PublicVoiceRead:
    gender, nationality = _voice_traits(voice)
    return PublicVoiceRead(
        id=voice.id,
        voice_name=voice.voice_name,
        is_custom_voice=voice.is_custom_voice,
        gender=gender,
        nationality=nationality,
        has_sample_audio=voice.has_sample_audio,
        sample_audio_url=voice.audio_file,
        is_enabled=voice.is_enabled,
    )

# -------------------------------------------------------------------
# AGENTS CRUD
# -------------------------------------------------------------------

@router.get(
    "/agents",
    response_model=PublicPaginatedResponse[PublicAgentListRead],
    description=(
        "Lists this account's agents. Supports filtering with `agent_name` (partial, "
        "case-insensitive match on agent name), `voice` (partial, case-insensitive "
        "match on voice name), and `is_enabled` (exact match). Supports sorting via "
        "`sort_by` (`created_at`, `modified_at`, `agent_name`, `kb_count`, `tool_count`, "
        "`conversation_count`) and `sort_order` (`asc`, `desc`)."
    ),
)
async def list_agents(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    agent_name: Optional[str] = Query(None, description="Filter by partial agent name (case-insensitive)"),
    voice: Optional[str] = Query(None, description="Filter by partial voice name (case-insensitive)"),
    is_enabled: Optional[bool] = Query(None, description="Filter by whether the agent is enabled", examples=[True]),
    sort_by: Literal[
        "created_at", "modified_at", "agent_name", "kb_count", "tool_count", "conversation_count"
    ] = Query("created_at", description="Field to sort agents by"),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort direction"),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    skip = (page - 1) * size
    with db():
        query = db.session.query(AgentModel).filter(AgentModel.user_id == current_user.id)
        if agent_name:
            query = query.filter(AgentModel.agent_name.ilike(f"%{agent_name}%"))
        if voice:
            query = query.join(AgentModel.voice).filter(VoiceModel.voice_name.ilike(f"%{voice}%"))
        if is_enabled is not None:
            query = query.filter(AgentModel.is_enabled.is_(is_enabled))

        # kb_count/tool_count/conversation_count live on other tables - join in
        # grouped subqueries (same shape as agents.py's get_all_agents) so ORDER BY
        # happens in SQL, before LIMIT/OFFSET, instead of only sorting the page.
        if sort_by in {"kb_count", "tool_count", "conversation_count"}:
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
                    func.count(ConversationsModel.id).label("cnt"),
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
            sort_col = {
                "kb_count": func.coalesce(kb_sub.c.cnt, 0),
                "tool_count": func.coalesce(tool_sub.c.cnt, 0),
                "conversation_count": func.coalesce(conv_sub.c.cnt, 0),
            }[sort_by]
        elif sort_by == "modified_at":
            sort_col = AgentModel.modified_at
        elif sort_by == "agent_name":
            sort_col = AgentModel.agent_name
        else:
            sort_col = AgentModel.created_at

        query = query.order_by(
            sort_col.asc() if sort_order == "asc" else sort_col.desc(),
            AgentModel.id.desc(),
        )

        total = query.count()
        total_pages = math.ceil(total / size) if total else 0
        agents = query.offset(skip).limit(size).all()

        agent_ids = [a.id for a in agents]
        kb_counts = (
            {
                row[0]: row[1]
                for row in db.session.query(
                    PersonalKnowledgeBaseAgentBridgeModel.agent_id, func.count(PersonalKnowledgeBaseAgentBridgeModel.kb_id)
                )
                .filter(PersonalKnowledgeBaseAgentBridgeModel.agent_id.in_(agent_ids))
                .group_by(PersonalKnowledgeBaseAgentBridgeModel.agent_id)
                .all()
            }
            if agent_ids else {}
        )
        tool_counts = (
            {
                row[0]: row[1]
                for row in db.session.query(
                    AgentFunctionBridgeModel.agent_id, func.count(AgentFunctionBridgeModel.function_id)
                )
                .filter(AgentFunctionBridgeModel.agent_id.in_(agent_ids))
                .group_by(AgentFunctionBridgeModel.agent_id)
                .all()
            }
            if agent_ids else {}
        )
        conversation_counts = (
            {
                row[0]: row[1]
                for row in db.session.query(
                    ConversationsModel.agent_id, func.count(ConversationsModel.id)
                )
                .filter(ConversationsModel.agent_id.in_(agent_ids))
                .group_by(ConversationsModel.agent_id)
                .all()
            }
            if agent_ids else {}
        )
        # Leads aren't linked to agents directly - they hang off the widget that
        # captured them (WidgetLeadModel.widget_id -> WidgetModel.agent_id).
        leads_counts = (
            {
                row[0]: row[1]
                for row in db.session.query(WidgetModel.agent_id, func.count(WidgetLeadModel.id))
                .join(WidgetModel, WidgetLeadModel.widget_id == WidgetModel.id)
                .filter(WidgetModel.agent_id.in_(agent_ids))
                .group_by(WidgetModel.agent_id)
                .all()
            }
            if agent_ids else {}
        )

        items = [
            agent_to_list_read(
                a,
                kb_count=kb_counts.get(a.id, 0),
                tool_count=tool_counts.get(a.id, 0),
                conversation_count=conversation_counts.get(a.id, 0),
                leads_count=leads_counts.get(a.id, 0),
                is_first_call_pending=(a.id not in conversation_counts),
            )
            for a in agents
        ]
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=items,
        )

@router.get("/agents/{agent_id}", response_model=PublicAgentRead)
async def get_agent(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        from app_v2.routers.agents import prune_stale_agent_transfers
        prune_stale_agent_transfers(agent, db.session)

        return agent_to_read(agent)

@router.post("/agents", response_model=PublicAgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    agent_in: PublicAgentCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    # Reusing original creation logic from agents.py would be ideal, but for public API 
    # we'll implement it here to ensure it uses the public auth and tracking.
    # (Implementation logic would be identical to agents.create_agent but restricted to the specific user)
    # Since it's a lot of code, I'll refer to original implementation and adapt.
    
    user_id = current_user.id
    with db():
        voice = db.session.query(VoiceModel).filter(
            VoiceModel.id == agent_in.voice,
            or_(VoiceModel.user_id == user_id, VoiceModel.user_id.is_(None)),
        ).first()
        if not voice or not voice.elevenlabs_voice_id:
            raise HTTPException(status_code=400, detail="Invalid voice id")
        if not voice.is_enabled:
            raise HTTPException(status_code=400, detail="This voice is disabled and cannot be used to create an agent")
        if not voice.has_sample_audio:
            raise HTTPException(status_code=400, detail="This voice has no sample audio available and cannot be used to create an agent")

        ai_model = db.session.query(AIModels).filter(AIModels.id == agent_in.ai_model).first()
        if not ai_model:
            raise HTTPException(status_code=400, detail="Invalid AI model id")
        if ai_model.model_name == CUSTOM_LLM_MODEL_NAME:
            raise HTTPException(status_code=400, detail="The custom-llm model cannot be used to create an agent via this API")

        language = db.session.query(LanguageModel).filter(LanguageModel.id == agent_in.language).first()
        if not language:
            raise HTTPException(status_code=400, detail="Invalid language id")

        # Same uniqueness rule as update_agent_public below: no other agent of
        # this user may share the name (case-insensitive). Checked before the
        # ElevenLabs call so a rejected create never leaves an orphaned
        # ElevenLabs-side agent with no local row.
        name_taken = db.session.query(AgentModel).filter(
            func.lower(AgentModel.agent_name) == agent_in.agent_name.lower(),
            AgentModel.user_id == user_id,
        ).first()
        if name_taken:
            raise HTTPException(status_code=400, detail="Agent with this name already exists")

        # -------------------------------------------------
        # Personal KB & Tools validation and lookup
        #
        # `knowledgebase` attaches personal KB items (PersonalKnowledgeBaseModel
        # — the self-hosted, FAISS-backed KB also exposed at GET
        # /api/v2/public/personal-kb), not the legacy ElevenLabs-native
        # KnowledgeBaseModel. Personal KB items don't feed ElevenLabs'
        # `knowledge_base` field at all — attaching one instead provisions
        # this agent's own search_personal_knowledge_base tool (see
        # ensure_personal_kb_tool_for_agent below, called once the agent exists).
        # -------------------------------------------------
        kb_ids_ordered = []
        if agent_in.knowledgebase:
            raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
            kb_ids_ordered = list(dict.fromkeys(raw_ids))
            kb_records = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id.in_(kb_ids_ordered),
                PersonalKnowledgeBaseModel.user_id == user_id,
            ).all()
            kb_map = {kb.id: kb for kb in kb_records}
            missing_ids = set(kb_ids_ordered) - set(kb_map.keys())
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Knowledge Base IDs not found: {list(missing_ids)}")

        el_tool_ids = []
        tool_ids_ordered = []
        if agent_in.tools:
            raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
            tool_ids_ordered = list(dict.fromkeys(raw_ids))

            # search_personal_knowledge_base is provisioned exclusively via
            # `knowledgebase` (see ensure_personal_kb_tool_for_agent below) —
            # never something a caller adds directly through `tools`.
            system_managed_ids = [
                row.id for row in db.session.query(FunctionModel.id).filter(
                    FunctionModel.id.in_(tool_ids_ordered),
                    FunctionModel.is_system_managed.is_(True),
                ).all()
            ]
            if system_managed_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tool IDs {system_managed_ids} are managed automatically (via `knowledgebase`) and cannot be set directly through `tools`",
                )

            tool_records = db.session.query(FunctionModel).filter(
                FunctionModel.id.in_(tool_ids_ordered),
                FunctionModel.elevenlabs_tool_id.isnot(None),
                or_(FunctionModel.user_id == user_id, FunctionModel.user_id.is_(None))
            ).all()
            tool_map = {tool.id: tool for tool in tool_records}
            missing_ids = set(tool_ids_ordered) - set(tool_map.keys())
            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Some Tool IDs not found or accessible: {list(missing_ids)}")
            for tool_id in tool_ids_ordered:
                el_tool_ids.append(tool_map[tool_id].elevenlabs_tool_id)

        from app_v2.routers.agents import transform_built_in_tools, prompt_requires_timezone, extract_prompt_variable_names

        full_built_in_tools = to_internal_built_in_tools(agent_in.built_in_tools)
        transformed_built_in = transform_built_in_tools(full_built_in_tools, db.session, user_id)

        if prompt_requires_timezone(agent_in.system_prompt) and not agent_in.timezone:
            raise HTTPException(
                status_code=400,
                detail="timezone is required when the system prompt uses {{system__time}}, {{system__time_utc}}, or {{system__timezone}}"
            )

        # Merge in any {{var_name}} placeholder found in the prompt that the
        # caller didn't explicitly declare in `variables`, so it still gets
        # persisted (and sent to ElevenLabs) as a placeholder variable instead
        # of being silently left unresolved — "test" until the caller sets
        # the real value via a later PUT.
        merged_variables = dict(agent_in.variables or {})
        for var_name in extract_prompt_variable_names(agent_in.system_prompt):
            merged_variables.setdefault(var_name, "test")

        # Create in ElevenLabs — no `knowledge_base=`: personal KB items don't
        # feed ElevenLabs' native knowledge_base field, they provision this
        # agent's own search_personal_knowledge_base tool once it exists (see
        # ensure_personal_kb_tool_for_agent below).
        el_client = ElevenLabsAgent()
        el_response = el_client.create_agent(
            name=agent_in.agent_name,
            voice_id=voice.elevenlabs_voice_id,
            prompt=agent_in.system_prompt,
            first_message=agent_in.first_message or "Hello!",
            language=language.lang_code,
            llm_model=ai_model.model_name,
            tool_ids=el_tool_ids,
            dynamic_variables=merged_variables,
            built_in_tools=transformed_built_in,
            timezone=agent_in.timezone
        )
        if not el_response.status:
            friendly_detail = describe_agent_sync_error(el_response.error_message)
            raise HTTPException(status_code=424, detail=friendly_detail or "Failed to create agent with the voice provider")

        elevenlabs_agent_id = el_response.data.get("agent_id")

        new_agent = AgentModel(
            agent_name=agent_in.agent_name,
            first_message=agent_in.first_message,
            system_prompt=agent_in.system_prompt,
            user_id=user_id,
            agent_voice=voice.id,
            elevenlabs_agent_id=elevenlabs_agent_id,
            built_in_tools=full_built_in_tools.model_dump() if full_built_in_tools else {},
            timezone=agent_in.timezone
        )
        db.session.add(new_agent)
        db.session.flush()

        # Best-effort store of the expected per-minute LLM price (live cutoff only).
        new_agent.llm_price_per_minute = el_client.get_llm_price_per_minute(
            elevenlabs_agent_id, ai_model.model_name
        )

        db.session.add(AgentAIModelBridge(agent_id=new_agent.id, ai_model_id=ai_model.id))
        db.session.add(AgentLanguageBridge(agent_id=new_agent.id, lang_id=language.id))

        for kb_id in kb_ids_ordered:
            db.session.add(PersonalKnowledgeBaseAgentBridgeModel(agent_id=new_agent.id, kb_id=kb_id))
        for tool_id in tool_ids_ordered:
            db.session.add(AgentFunctionBridgeModel(agent_id=new_agent.id, function_id=tool_id))

        for key, value in merged_variables.items():
            db.session.add(VariablesModel(agent_id=new_agent.id, variable_name=key, variable_value=value))

        db.session.commit()
        db.session.refresh(new_agent)

        if kb_ids_ordered:
            try:
                ensure_personal_kb_tool_for_agent(new_agent.id)
            except Exception as e:
                logger.warning(f"Failed to provision personal KB tool for new agent {new_agent.id}: {e}")

        return agent_to_read(new_agent)

@router.put("/agents/{agent_id}", response_model=PublicAgentRead)
async def update_agent_public(
    agent_id: int,
    agent_in: PublicAgentUpdate,
    request: Request,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    user_id = current_user.id
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == agent_id, AgentModel.user_id == user_id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        # True PUT: agent_name/first_message/system_prompt/voice/ai_model/
        # language are required (see PublicAgentUpdate) and always applied
        # below — unlike the internal PATCH-style AgentUpdate, omitting one
        # is a validation error instead of silently keeping the old value.
        name_taken = db.session.query(AgentModel).filter(
            func.lower(AgentModel.agent_name) == agent_in.agent_name.lower(),
            AgentModel.user_id == user_id,
            AgentModel.id != agent_id,
        ).first()
        if name_taken:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent with this name already exists",
            )

        voice = db.session.query(VoiceModel).filter(
            VoiceModel.id == agent_in.voice,
            or_(VoiceModel.user_id == user_id, VoiceModel.user_id.is_(None)),
        ).first()
        if not voice or not voice.elevenlabs_voice_id:
            raise HTTPException(status_code=400, detail="Invalid voice id")
        if not voice.is_enabled:
            raise HTTPException(status_code=400, detail="This voice is disabled and cannot be used for an agent")
        if not voice.has_sample_audio:
            raise HTTPException(status_code=400, detail="This voice has no sample audio available and cannot be used for an agent")

        ai_model = db.session.query(AIModels).filter(AIModels.id == agent_in.ai_model).first()
        if not ai_model:
            raise HTTPException(status_code=400, detail="Invalid AI model id")
        if ai_model.model_name == CUSTOM_LLM_MODEL_NAME:
            raise HTTPException(status_code=400, detail="The custom-llm model cannot be used for an agent via this API")

        language = db.session.query(LanguageModel).filter(LanguageModel.id == agent_in.language).first()
        if not language:
            raise HTTPException(status_code=400, detail="Invalid language id")

        # Re-apply (or keep absent) the personal-KB tool prompt block based
        # on this agent's actual current tool state — the client never sees
        # the block (see agent_to_read), so it can't be trusted to round-trip
        # it correctly on its own.
        new_prompt = apply_prompt_block_state(agent.id, agent_in.system_prompt)

        from app_v2.routers.agents import (
            transform_built_in_tools, prompt_requires_timezone, extract_prompt_variable_names,
        )

        if prompt_requires_timezone(new_prompt) and not agent_in.timezone:
            raise HTTPException(
                status_code=400,
                detail="timezone is required when the system prompt uses {{system__time}}, {{system__time_utc}}, or {{system__timezone}}"
            )

        agent.agent_name = agent_in.agent_name
        agent.system_prompt = new_prompt
        agent.first_message = agent_in.first_message
        agent.timezone = agent_in.timezone
        agent.agent_voice = voice.id

        # ---- is_enabled ----
        # Mirrors the internal PATCH endpoint's cascade (see agents.py
        # update_agent): disabling/enabling an agent disables/enables all of
        # its widgets and web agent pages along with it, since a widget or
        # web agent page backed by a disabled agent can't take calls anyway.
        # request.state.public_message surfaces the affected counts back to
        # the caller (see PublicAPIRoute.custom above) — silently flipping
        # widgets/web agents the caller didn't ask about would be confusing.
        if agent_in.is_enabled is not None and agent_in.is_enabled != agent.is_enabled:
            if agent_in.is_enabled:
                check_can_enable_resource(user_id, "ai_voice_agents", allow_coin_fallback=True)

            widget_count = db.session.query(WidgetModel).filter(WidgetModel.agent_id == agent.id).count()
            web_agent_count = db.session.query(WebAgentPageModel).filter(WebAgentPageModel.agent_id == agent.id).count()

            db.session.query(WidgetModel).filter(
                WidgetModel.agent_id == agent.id
            ).update({WidgetModel.is_enabled: agent_in.is_enabled})
            db.session.query(WebAgentPageModel).filter(
                WebAgentPageModel.agent_id == agent.id
            ).update({WebAgentPageModel.is_enabled: agent_in.is_enabled})

            agent.is_enabled = agent_in.is_enabled

            state_word = "enabled" if agent_in.is_enabled else "disabled"
            cascade_message = (
                f"The agent has {widget_count} widgets and {web_agent_count} web agents "
                f"and they are also {state_word} now."
            )
            request.state.public_message = cascade_message
            request.state.public_detail = cascade_message

        el_update_params = {
            "name": agent_in.agent_name,
            "voice_id": voice.elevenlabs_voice_id,
            "prompt": new_prompt,
            "first_message": agent_in.first_message,
            "language": language.lang_code,
            "llm_model": ai_model.model_name,
            "timezone": agent_in.timezone,
        }

        db.session.query(AgentAIModelBridge).filter(AgentAIModelBridge.agent_id == agent_id).delete()
        db.session.add(AgentAIModelBridge(agent_id=agent_id, ai_model_id=ai_model.id))

        db.session.query(AgentLanguageBridge).filter(AgentLanguageBridge.agent_id == agent_id).delete()
        db.session.add(AgentLanguageBridge(agent_id=agent_id, lang_id=language.id))

        # No phone handling here — phone-to-agent assignment only happens
        # from the /phone dashboard page now, not inline at agent update.

        # ---- Knowledge Base Update ----
        # `knowledgebase` attaches personal KB items (PersonalKnowledgeBaseModel
        # — the self-hosted, FAISS-backed KB), not the legacy ElevenLabs-native
        # KnowledgeBaseModel. Personal KB doesn't feed ElevenLabs' native
        # `knowledge_base` field — it provisions/removes this agent's own
        # search_personal_knowledge_base tool instead (see
        # ensure_personal_kb_tool_for_agent / remove_personal_kb_tool_from_agent_if_empty
        # below). Omit `knowledgebase` to leave the current attachments unchanged.
        personal_kb_updated = agent_in.knowledgebase is not None
        new_kb_ids_ordered = []
        if agent_in.knowledgebase is not None:
            raw_ids = [k.get("id") if isinstance(k, dict) else k for k in agent_in.knowledgebase]
            new_kb_ids_ordered = list(dict.fromkeys(raw_ids))

            kb_records = db.session.query(PersonalKnowledgeBaseModel).filter(
                PersonalKnowledgeBaseModel.id.in_(new_kb_ids_ordered),
                PersonalKnowledgeBaseModel.user_id == user_id,
            ).all()

            kb_map = {kb.id: kb for kb in kb_records}
            missing_ids = set(new_kb_ids_ordered) - set(kb_map.keys())

            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Knowledge Base IDs not found: {list(missing_ids)}")

            db.session.query(PersonalKnowledgeBaseAgentBridgeModel).filter(
                PersonalKnowledgeBaseAgentBridgeModel.agent_id == agent_id
            ).delete()
            for kb_id in new_kb_ids_ordered:
                db.session.add(PersonalKnowledgeBaseAgentBridgeModel(agent_id=agent_id, kb_id=kb_id))

        # ---- Tools Update ----
        if agent_in.tools is not None:
            raw_ids = [t.get("id") if isinstance(t, dict) else t for t in agent_in.tools]
            tool_ids_ordered = list(dict.fromkeys(raw_ids))

            # search_personal_knowledge_base is provisioned exclusively via
            # `knowledgebase` (see ensure_personal_kb_tool_for_agent /
            # remove_personal_kb_tool_from_agent_if_empty below) — reject any
            # attempt to set it directly, and preserve whatever's already
            # bound below so replacing `tools` can never silently detach it.
            system_managed_ids = [
                row.id for row in db.session.query(FunctionModel.id).filter(
                    FunctionModel.id.in_(tool_ids_ordered),
                    FunctionModel.is_system_managed.is_(True),
                ).all()
            ]
            if system_managed_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tool IDs {system_managed_ids} are managed automatically (via `knowledgebase`) and cannot be set directly through `tools`",
                )

            tool_records = db.session.query(FunctionModel).filter(
                FunctionModel.id.in_(tool_ids_ordered),
                FunctionModel.elevenlabs_tool_id.isnot(None),
                or_(
                    FunctionModel.user_id == user_id,
                    FunctionModel.user_id.is_(None)
                )
            ).all()

            tool_map = {tool.id: tool for tool in tool_records}
            missing_ids = set(tool_ids_ordered) - set(tool_map.keys())

            if missing_ids:
                raise HTTPException(status_code=400, detail=f"Some Tool IDs not found or synced: {list(missing_ids)}")

            el_tool_ids = []
            for tool_id in tool_ids_ordered:
                el_tool_ids.append(tool_map[tool_id].elevenlabs_tool_id)

            existing_system_bridges = (
                db.session.query(AgentFunctionBridgeModel, FunctionModel.elevenlabs_tool_id)
                .join(FunctionModel, FunctionModel.id == AgentFunctionBridgeModel.function_id)
                .filter(
                    AgentFunctionBridgeModel.agent_id == agent_id,
                    FunctionModel.is_system_managed.is_(True),
                )
                .all()
            )
            preserved_tool_ids = [bridge.function_id for bridge, _ in existing_system_bridges]
            preserved_el_tool_ids = [el_id for _, el_id in existing_system_bridges if el_id]

            el_update_params["tool_ids"] = el_tool_ids + preserved_el_tool_ids

            db.session.query(AgentFunctionBridgeModel).filter(
                AgentFunctionBridgeModel.agent_id == agent_id
            ).delete()
            for tool_id in tool_ids_ordered + preserved_tool_ids:
                db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=tool_id))

        # ---- Built-in Tools Update ----
        if agent_in.built_in_tools is not None:
            full_built_in_tools = to_internal_built_in_tools(agent_in.built_in_tools)
            # transform_built_in_tools may drop invalid transfers (e.g. an agent_id
            # that no longer resolves) from full_built_in_tools in place, so run
            # it before the model_dump() to keep the persisted config in sync with
            # what was actually sent to ElevenLabs.
            el_update_params["built_in_tools"] = transform_built_in_tools(full_built_in_tools, db.session, user_id, current_agent_id=agent_id)
            agent.built_in_tools = full_built_in_tools.model_dump()

        # ---- Variables Update ----
        # True PUT: `variables` is replaced wholesale with whatever the caller
        # sends (matching create_agent's merged_variables), then any
        # {{placeholder}} the system prompt references that the caller didn't
        # explicitly set gets added defaulting to "test". A variable that's
        # neither passed here nor referenced by the current prompt does not
        # survive from the old row — that's the whole point of PUT.
        prompt_var_names = extract_prompt_variable_names(new_prompt)
        existing_variables = {v.variable_name: v.variable_value for v in agent.variables}
        explicit_variables = agent_in.variables or {}

        synced_variables = dict(explicit_variables)
        for var_name in prompt_var_names:
            synced_variables.setdefault(var_name, "test")

        if synced_variables != existing_variables:
            db.session.query(VariablesModel).filter(
                VariablesModel.agent_id == agent_id
            ).delete()
            for key, value in synced_variables.items():
                db.session.add(VariablesModel(agent_id=agent_id, variable_name=key, variable_value=value))
            el_update_params["dynamic_variables"] = synced_variables

        # ---- Sync with ElevenLabs ----
        if agent.elevenlabs_agent_id:
            try:
                el_client = ElevenLabsAgent()
                el_response = el_client.update_agent(
                    agent_id=agent.elevenlabs_agent_id,
                    **el_update_params
                )
                if not el_response.status:
                    db.session.rollback()
                    friendly_detail = describe_agent_sync_error(el_response.error_message)
                    raise HTTPException(
                        status_code=424,
                        detail=friendly_detail or f"Failed to update agent: {el_response.error_message}"
                    )

                # Refresh the stored LLM price now that ElevenLabs has the new
                # config (best-effort; prompt/KB/RAG/model all affect it).
                agent.llm_price_per_minute = el_client.get_llm_price_per_minute(
                    agent.elevenlabs_agent_id, ai_model.model_name
                )
            except HTTPException:
                raise
            except Exception as e:
                db.session.rollback()
                raise HTTPException(
                    status_code=424,
                    detail=f"Failed to update agent due to an unexpected error: {str(e)}"
                )

        db.session.commit()
        db.session.refresh(agent)

        # If this request touched `knowledgebase`, provision/remove this
        # agent's search_personal_knowledge_base tool to match the new
        # attachment set (mirrors what the dedicated attach/detach endpoints
        # in personal_knowledge_base.py do for a single item at a time).
        if personal_kb_updated:
            try:
                if new_kb_ids_ordered:
                    ensure_personal_kb_tool_for_agent(agent.id)
                else:
                    remove_personal_kb_tool_from_agent_if_empty(agent.id)
            except Exception as e:
                logger.warning(f"Failed to sync personal KB tool state for agent {agent.id}: {e}")

        # No extra resync call here: unlike the internal PATCH endpoint, the
        # ---- Tools Update ---- block above always preserves any pre-existing
        # system-managed (personal KB) tool bridge into el_update_params
        # itself (see preserved_tool_ids/preserved_el_tool_ids) whenever
        # `tools` is touched, and ensure_/remove_personal_kb_tool_* already
        # push their own resync when `knowledgebase` is touched. A trailing
        # unconditional resync here would just repeat one of those two calls
        # with an identical payload on every single PUT.

        return agent_to_read(agent)

@router.delete("/agents/{agent_id}")
async def delete_agent_public(
    agent_id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        if agent.elevenlabs_agent_id:
            ElevenLabsAgent().delete_agent(agent.elevenlabs_agent_id)

    try:
        delete_agent_personal_kb_tool(agent_id)
    except Exception as e:
        logger.warning(f"Failed to clean up personal KB tool for deleted agent {agent_id}: {e}")

    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == agent_id, AgentModel.user_id == current_user.id
        ).first()
        if agent:
            db.session.delete(agent)
            db.session.commit()
    return None

# -------------------------------------------------------------------
# WidgetS CRUD
# -------------------------------------------------------------------

@router.get(
    "/widgets",
    response_model=PublicPaginatedResponse[WidgetListResponse],
    description=(
        "Lists this account's widgets. Supports filtering with `widget_name` (partial, "
        "case-insensitive), `agent_name` (partial, case-insensitive match on the "
        "linked agent's name), and `is_enabled` (exact match). Supports sorting via "
        "`sort_by` (`created_at`, `updated_at`, `widget_name`) and `sort_order` "
        "(`asc`, `desc`)."
    ),
)
async def list_widgets(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    widget_name: Optional[str] = Query(None, description="Filter by partial widget name (case-insensitive)", examples=["Sales Widget"]),
    agent_name: Optional[str] = Query(None, description="Filter by partial linked agent name (case-insensitive)", examples=["Sales Assistant"]),
    is_enabled: Optional[bool] = Query(None, description="Filter by whether the widget is enabled", examples=[True]),
    sort_by: Literal["created_at", "updated_at", "widget_name"] = Query("created_at", description="Field to sort widgets by"),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort direction"),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    skip = (page - 1) * size
    base_url = str(request.base_url).rstrip("/")
    with db():
        query = db.session.query(WidgetModel).filter(WidgetModel.user_id == current_user.id)
        if widget_name:
            query = query.filter(WidgetModel.widget_name.ilike(f"%{widget_name}%"))
        if is_enabled is not None:
            query = query.filter(WidgetModel.is_enabled.is_(is_enabled))
        if agent_name:
            query = query.join(WidgetModel.agent).filter(AgentModel.agent_name.ilike(f"%{agent_name}%"))

        sort_col = {
            "updated_at": WidgetModel.modified_at,
            "widget_name": WidgetModel.widget_name,
        }.get(sort_by, WidgetModel.created_at)

        total = query.count()
        widgets = (
            query.order_by(
                sort_col.asc() if sort_order == "asc" else sort_col.desc(),
                WidgetModel.id.desc(),
            )
            .offset(skip).limit(size).all()
        )

        items = [
            WidgetListResponse(
                id=wa.id,
                widget_name=wa.widget_name,
                public_id=wa.public_id,
                shareable_link=f"{base_url}/api/v2/widget/preview/{wa.public_id}",
                is_enabled=wa.is_enabled,
                created_at=wa.created_at,
                updated_at=wa.modified_at,
                agent_id=wa.agent_id,
                agent_name=wa.agent.agent_name
            ) for wa in widgets
        ]
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=items,
        )

@router.get("/widgets/{public_id}", response_model=WidgetConfigResponse)
async def get_widget(
    public_id: str,
    request: Request,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        wa = db.session.query(WidgetModel).filter(
            WidgetModel.public_id == public_id, WidgetModel.user_id == current_user.id
        ).first()
        if not wa:
            raise HTTPException(status_code=404, detail="Widget not found")
        return widget_to_response(wa, request)

@router.post("/widgets", response_model=WidgetConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_widget(
    wa_in: PublicWidgetConfig,
    request: Request,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == wa_in.agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not agent.is_enabled:
            raise HTTPException(status_code=403, detail="Agent is disabled")

        existing = db.session.query(WidgetModel).filter(
            WidgetModel.agent_id == wa_in.agent_id,
            func.lower(WidgetModel.widget_name) == wa_in.widget_name.lower()
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Widget with same name already exists for this Voice Agent.")

        new_wa = WidgetModel(
            user_id=current_user.id,
            agent_id=wa_in.agent_id,
            widget_name=wa_in.widget_name,
            widget_title=wa_in.appearance.widget_title,
            widget_subtitle=wa_in.appearance.widget_subtitle,
            primary_color=wa_in.appearance.primary_color,
            position=wa_in.appearance.position,
            show_branding=wa_in.appearance.show_branding,
            enable_prechat=wa_in.prechat.enable_prechat,
            require_name=wa_in.prechat.require_name,
            require_email=wa_in.prechat.require_email,
            require_phone=wa_in.prechat.require_phone,
            custom_fields=[f.model_dump() for f in wa_in.prechat.custom_fields]
        )
        db.session.add(new_wa)
        db.session.commit()
        db.session.refresh(new_wa)
        return widget_to_response(new_wa, request)

@router.put("/widgets/{public_id}", response_model=WidgetConfigResponse)
async def update_widget(
    public_id: str,
    wa_in: PublicWidgetConfigUpdate,
    request: Request,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    with db():
        wa = db.session.query(WidgetModel).filter(
            WidgetModel.public_id == public_id, WidgetModel.user_id == current_user.id
        ).first()
        if not wa:
            raise HTTPException(status_code=404, detail="Widget not found")

        update_data = wa_in.model_dump(exclude_unset=True)

        if "agent_id" in update_data:
            agent = db.session.query(AgentModel).filter(
                AgentModel.id == update_data["agent_id"], AgentModel.user_id == current_user.id
            ).first()
            if not agent:
                raise HTTPException(status_code=403, detail="Agent does not belong to user")
            wa.agent_id = update_data["agent_id"]

        if "widget_name" in update_data:
            wa.widget_name = update_data["widget_name"]

        if "is_enabled" in update_data:
            if update_data["is_enabled"] and not wa.is_enabled:
                voice_agent = db.session.query(AgentModel).filter(AgentModel.id == wa.agent_id).first()
                if not voice_agent or not voice_agent.is_enabled:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot enable widget: its Voice Agent is disabled",
                    )
                check_can_enable_resource(current_user.id, "widget_agent", allow_coin_fallback=True)
            wa.is_enabled = update_data["is_enabled"]

        if "appearance" in update_data:
            appearance_data = update_data["appearance"]
            if "widget_title" in appearance_data: wa.widget_title = appearance_data["widget_title"]
            if "widget_subtitle" in appearance_data: wa.widget_subtitle = appearance_data["widget_subtitle"]
            if "primary_color" in appearance_data: wa.primary_color = appearance_data["primary_color"]
            if "position" in appearance_data: wa.position = appearance_data["position"]
            if "show_branding" in appearance_data: wa.show_branding = appearance_data["show_branding"]

        if "prechat" in update_data and update_data.get("prechat") is not None:
            prechat_data = update_data["prechat"]
            if "enable_prechat" in prechat_data: wa.enable_prechat = prechat_data["enable_prechat"]
            if "require_name" in prechat_data: wa.require_name = prechat_data["require_name"]
            if "require_email" in prechat_data: wa.require_email = prechat_data["require_email"]
            if "require_phone" in prechat_data: wa.require_phone = prechat_data["require_phone"]
            if "custom_fields" in prechat_data: wa.custom_fields = prechat_data["custom_fields"] or []

        db.session.commit()
        db.session.refresh(wa)
        return widget_to_response(wa, request)

@router.delete("/widgets/{public_id}")
async def delete_widget(
    public_id: str,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        wa = db.session.query(WidgetModel).filter(
            WidgetModel.public_id == public_id, WidgetModel.user_id == current_user.id
        ).first()
        if not wa:
            raise HTTPException(status_code=404, detail="Widget not found")
        db.session.delete(wa)
        db.session.commit()
    return None

# -------------------------------------------------------------------
# WEB AGENTS CRUD
# -------------------------------------------------------------------

@router.get("/web-agents", response_model=PublicPaginatedResponse[WebAgentListResponse])
async def list_web_agents_public(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.web_agent)
    skip = (page - 1) * size
    with db():
        from app_v2.routers.web_agent_config import _shareable_link

        query = db.session.query(WebAgentPageModel).filter(WebAgentPageModel.user_id == current_user.id)
        total = query.count()
        web_agents = (
            query.order_by(WebAgentPageModel.created_at.desc(), WebAgentPageModel.id.desc())
            .offset(skip).limit(size).all()
        )
        items = [
            WebAgentListResponse(
                id=wa.id,
                public_id=wa.public_id,
                web_agent_name=wa.web_agent_name,
                is_enabled=wa.is_enabled,
                bg_color=wa.bg_color,
                agent_position=wa.agent_position,
                agent_id=wa.agent_id,
                agent_name=wa.agent.agent_name if wa.agent else "",
                widget_id=wa.widget_id,
                widget_name=wa.widget.widget_name if wa.widget else "",
                shareable_link=_shareable_link(request, wa.public_id),
                created_at=wa.created_at,
            )
            for wa in web_agents
        ]
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=items,
        )


@router.get("/web-agents/{public_id}", response_model=WebAgentResponse)
async def get_web_agent_public(
    public_id: str,
    request: Request,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.web_agent)
    with db():
        from app_v2.routers.web_agent_config import _to_response

        web_agent = db.session.query(WebAgentPageModel).filter(
            WebAgentPageModel.public_id == public_id, WebAgentPageModel.user_id == current_user.id
        ).first()
        if not web_agent:
            raise HTTPException(status_code=404, detail="Web agent not found")
        return _to_response(request, web_agent)


@router.post("/web-agents", response_model=WebAgentResponse, status_code=status.HTTP_201_CREATED)
async def create_web_agent_public(
    payload: WebAgentCreate,
    request: Request,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.web_agent)
    with db():
        from app_v2.routers.web_agent_config import _to_response, _validate_widget_belongs_to_agent

        agent = db.session.query(AgentModel).filter(
            AgentModel.id == payload.agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent:
            raise HTTPException(status_code=403, detail="Agent does not belong to user")
        if not agent.is_enabled:
            raise HTTPException(status_code=403, detail="Agent is disabled")

        _validate_widget_belongs_to_agent(current_user.id, payload.widget_id, payload.agent_id)

        web_agent = WebAgentPageModel(
            public_id=str(uuid.uuid4()),
            user_id=current_user.id,
            agent_id=payload.agent_id,
            widget_id=payload.widget_id,
            web_agent_name=payload.web_agent_name,
            bg_color=payload.bg_color,
            agent_position=payload.agent_position,
        )
        db.session.add(web_agent)
        db.session.commit()
        db.session.refresh(web_agent)

        log_activity(
            user_id=current_user.id,
            event_type="web_agent_created",
            description=f"Created web agent: {web_agent.web_agent_name}",
            metadata={"web_agent_id": web_agent.id, "public_id": web_agent.public_id},
        )

        return _to_response(request, web_agent)


@router.put("/web-agents/{public_id}", response_model=WebAgentResponse)
async def update_web_agent_public(
    public_id: str,
    payload: WebAgentUpdate,
    request: Request,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.web_agent)
    with db():
        from app_v2.routers.web_agent_config import _to_response, _validate_widget_belongs_to_agent

        web_agent = db.session.query(WebAgentPageModel).filter(
            WebAgentPageModel.public_id == public_id, WebAgentPageModel.user_id == current_user.id
        ).first()
        if not web_agent:
            raise HTTPException(status_code=404, detail="Web agent not found")

        update_data = payload.model_dump(exclude_unset=True)

        new_agent_id = update_data.get("agent_id", web_agent.agent_id)
        if "agent_id" in update_data:
            agent = db.session.query(AgentModel).filter(
                AgentModel.id == new_agent_id, AgentModel.user_id == current_user.id
            ).first()
            if not agent:
                raise HTTPException(status_code=403, detail="Agent does not belong to user")
            web_agent.agent_id = new_agent_id

        if "widget_id" in update_data:
            _validate_widget_belongs_to_agent(current_user.id, update_data["widget_id"], new_agent_id)
            web_agent.widget_id = update_data["widget_id"]
        elif "agent_id" in update_data:
            # Agent changed but widget wasn't re-specified — the existing widget must
            # still belong to the (new) agent for the record to stay consistent.
            _validate_widget_belongs_to_agent(current_user.id, web_agent.widget_id, new_agent_id)

        if "web_agent_name" in update_data:
            web_agent.web_agent_name = update_data["web_agent_name"]
        if "bg_color" in update_data:
            web_agent.bg_color = update_data["bg_color"]
        if "agent_position" in update_data:
            web_agent.agent_position = update_data["agent_position"]

        if "is_enabled" in update_data:
            if update_data["is_enabled"] and not web_agent.is_enabled:
                voice_agent = db.session.query(AgentModel).filter(AgentModel.id == web_agent.agent_id).first()
                if not voice_agent or not voice_agent.is_enabled:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot enable web agent: its Voice Agent is disabled",
                    )
            web_agent.is_enabled = update_data["is_enabled"]

        db.session.commit()
        db.session.refresh(web_agent)

        log_activity(
            user_id=current_user.id,
            event_type="web_agent_updated",
            description=f"Updated web agent: {web_agent.web_agent_name}",
            metadata={"web_agent_id": web_agent.id, "public_id": web_agent.public_id},
        )

        return _to_response(request, web_agent)


@router.delete("/web-agents/{public_id}")
async def delete_web_agent_public(
    public_id: str,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.web_agent)
    with db():
        web_agent = db.session.query(WebAgentPageModel).filter(
            WebAgentPageModel.public_id == public_id, WebAgentPageModel.user_id == current_user.id
        ).first()
        if not web_agent:
            raise HTTPException(status_code=404, detail="Web agent not found")
        db.session.delete(web_agent)
        db.session.commit()
    return None

# -------------------------------------------------------------------
# TWILIO CONNECTORS CRUD
# -------------------------------------------------------------------

# Public Twilio connector APIs disabled — decorators commented out so they
# don't register as routes (hidden from Swagger, 404 for any caller). Code
# kept in place, not deleted, in case these need to come back.
# @router.get("/twilio-connectors", response_model=PublicPaginatedResponse[TwilioConnectorResponse])
async def list_twilio_connectors(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.phone_numbers)
    skip = (page - 1) * size
    with db():
        query = db.session.query(TwilioUserCreds).filter(TwilioUserCreds.user_id == current_user.id)
        total = query.count()
        connectors = (
            query.order_by(TwilioUserCreds.created_at.desc(), TwilioUserCreds.id.desc())
            .offset(skip).limit(size).all()
        )

        items = [twilio_connector_to_response(c) for c in connectors]
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=items,
        )

# @router.get("/twilio-connectors/{connector_id}", response_model=TwilioConnectorResponse)
async def get_twilio_connector(
    connector_id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.phone_numbers)
    with db():
        connector = db.session.query(TwilioUserCreds).filter(
            TwilioUserCreds.id == connector_id, TwilioUserCreds.user_id == current_user.id
        ).first()
        if not connector:
            raise HTTPException(status_code=404, detail="Twilio connector not found")
        return twilio_connector_to_response(connector)

# @router.post("/twilio-connectors", response_model=TwilioConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_twilio_connector(
    connector_in: TwilioConnectorCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.phone_numbers)
    with db():
        existing_name = db.session.query(TwilioUserCreds).filter(
            TwilioUserCreds.user_id == current_user.id,
            func.lower(TwilioUserCreds.name) == connector_in.name.lower()
        ).first()
        if existing_name:
            raise HTTPException(status_code=400, detail=f"A connector with the name '{existing_name.name}' already exists.")

        existing_sid = db.session.query(TwilioUserCreds).filter(
            TwilioUserCreds.user_id == current_user.id,
            func.lower(TwilioUserCreds.account_sid) == connector_in.account_sid.lower()
        ).first()
        if existing_sid:
            raise HTTPException(status_code=400, detail=f"This Twilio account is already connected as '{existing_sid.name}'.")

    try:
        client = TwilioClient(connector_in.account_sid, connector_in.auth_token)
        client.api.accounts(connector_in.account_sid).fetch()
    except TwilioRestException:
        raise HTTPException(status_code=400, detail="Invalid Twilio Account SID or Auth Token.")

    with db():
        new_connector = TwilioUserCreds(
            user_id=current_user.id,
            name=connector_in.name,
            account_sid=connector_in.account_sid,
            auth_token=encrypt_data(connector_in.auth_token),
        )
        db.session.add(new_connector)
        db.session.commit()
        db.session.refresh(new_connector)
        return twilio_connector_to_response(new_connector)

# @router.put("/twilio-connectors/{connector_id}", response_model=TwilioConnectorResponse)
async def update_twilio_connector(
    connector_id: int,
    connector_in: TwilioConnectorUpdate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.phone_numbers)

    if (connector_in.account_sid is None) != (connector_in.auth_token is None):
        raise HTTPException(status_code=400, detail="Both Account SID and Auth Token must be provided together to update credentials.")

    with db():
        connector = db.session.query(TwilioUserCreds).filter(
            TwilioUserCreds.id == connector_id, TwilioUserCreds.user_id == current_user.id
        ).first()
        if not connector:
            raise HTTPException(status_code=404, detail="Twilio connector not found")

        if connector_in.name is not None:
            existing_name = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.user_id == current_user.id,
                TwilioUserCreds.id != connector_id,
                func.lower(TwilioUserCreds.name) == connector_in.name.lower()
            ).first()
            if existing_name:
                raise HTTPException(status_code=400, detail=f"A connector with the name '{existing_name.name}' already exists.")

        if connector_in.account_sid is not None:
            existing_sid = db.session.query(TwilioUserCreds).filter(
                TwilioUserCreds.user_id == current_user.id,
                TwilioUserCreds.id != connector_id,
                func.lower(TwilioUserCreds.account_sid) == connector_in.account_sid.lower()
            ).first()
            if existing_sid:
                raise HTTPException(status_code=400, detail=f"This Twilio account is already connected as '{existing_sid.name}'.")

        if connector_in.account_sid is not None and connector_in.auth_token is not None:
            try:
                client = TwilioClient(connector_in.account_sid, connector_in.auth_token)
                client.api.accounts(connector_in.account_sid).fetch()
            except TwilioRestException:
                raise HTTPException(status_code=400, detail="Invalid Twilio Account SID or Auth Token.")
            connector.account_sid = connector_in.account_sid
            connector.auth_token = encrypt_data(connector_in.auth_token)

        if connector_in.name is not None:
            connector.name = connector_in.name

        db.session.commit()
        db.session.refresh(connector)
        return twilio_connector_to_response(connector)

# @router.delete("/twilio-connectors/{connector_id}")
async def delete_twilio_connector(
    connector_id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    require_feature_enabled(current_user.id, PlanFeatureEnum.phone_numbers)
    with db():
        connector = db.session.query(TwilioUserCreds).filter(
            TwilioUserCreds.id == connector_id, TwilioUserCreds.user_id == current_user.id
        ).first()
        if not connector:
            raise HTTPException(status_code=404, detail="Twilio connector not found")

        from app_v2.routers.agents import unassign_phone_numbers_for_connector
        unassign_phone_numbers_for_connector(db.session, current_user.id, connector)

        db.session.delete(connector)
        db.session.commit()
    return None

# -------------------------------------------------------------------
# LANGUAGES
# -------------------------------------------------------------------

@router.get(
    "/languages",
    response_model=PublicPaginatedResponse[LanguageRead],
    description=(
        "Lists supported languages. Each item's numeric `id` is the value to pass as "
        "`language` in POST /agents. Supports filtering with `lang_code` (partial, "
        "case-insensitive match on the code, e.g. `en`) and `language_name` (partial, "
        "case-insensitive match on the name, e.g. `English`)."
    ),
)
async def list_languages_public(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    lang_code: Optional[str] = Query(None, description="Filter by partial language code match (case-insensitive)", examples=["en"]),
    language_name: Optional[str] = Query(None, description="Filter by partial language name match (case-insensitive)", examples=["English"]),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    skip = (page - 1) * size
    with db():
        query = db.session.query(LanguageModel)
        if lang_code:
            query = query.filter(LanguageModel.lang_code.ilike(f"%{lang_code}%"))
        if language_name:
            query = query.filter(LanguageModel.language.ilike(f"%{language_name}%"))
        total = query.count()
        languages = query.order_by(LanguageModel.id.asc()).offset(skip).limit(size).all()
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=languages,
        )

@router.get("/languages/{id}", response_model=LanguageRead)
async def get_language_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        language = db.session.query(LanguageModel).filter(LanguageModel.id == id).first()
        if not language:
            raise HTTPException(status_code=404, detail="Language not found")
        return language

# -------------------------------------------------------------------
# VOICES
# -------------------------------------------------------------------

@router.get(
    "/voices",
    response_model=PublicPaginatedResponse[PublicVoiceListRead],
    description=(
        "Lists voices available to this account (enabled voices only). Supports "
        "filtering with `voice_name` (partial, case-insensitive), `gender` "
        "(`male`/`female`), and `nationality` (partial, case-insensitive). "
        "Each item's numeric `id` is the value to pass as `voice` in POST "
        "/agents — only voices with `has_sample_audio: true` can be used."
    ),
)
async def list_voices_public(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    voice_name: Optional[str] = Query(None, description="Filter by partial voice name (case-insensitive)"),
    gender: Optional[Literal["male", "female"]] = Query(None, description="Filter by voice gender"),
    nationality: Optional[str] = Query(None, description="Filter by partial nationality match (case-insensitive)"),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    skip = (page - 1) * size
    with db():
        query = (
            db.session.query(VoiceModel)
            .options(selectinload(VoiceModel.traits))
            .filter(
                or_(
                    VoiceModel.user_id == current_user.id,
                    VoiceModel.user_id.is_(None),
                ),
                VoiceModel.is_enabled.is_(True),
            )
        )
        if voice_name:
            query = query.filter(VoiceModel.voice_name.ilike(f"%{voice_name}%"))
        if gender or nationality:
            query = query.join(VoiceTraitsModel, VoiceModel.traits)
            if gender:
                query = query.filter(VoiceTraitsModel.gender == GenderEnum(gender))
            if nationality:
                query = query.filter(VoiceTraitsModel.nationality.ilike(f"%{nationality}%"))
        total = query.count()
        voices = query.order_by(VoiceModel.id.asc()).offset(skip).limit(size).all()
        items = [voice_to_list_read(v) for v in voices]
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=items,
        )

@router.get("/voices/{id}", response_model=PublicVoiceRead)
async def get_voice_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        voice = db.session.query(VoiceModel).options(selectinload(VoiceModel.traits)).filter(
            VoiceModel.id == id,
            VoiceModel.is_enabled.is_(True),
            or_(
                VoiceModel.user_id == current_user.id,
                VoiceModel.user_id.is_(None)
            )
        ).first()
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        return voice_to_read(voice)

# -------------------------------------------------------------------
# AI MODELS
# -------------------------------------------------------------------

@router.get(
    "/ai-models",
    response_model=PublicPaginatedResponse[AIModelRead],
    description=(
        "Lists available AI models, sorted by id. Supports filtering with "
        "`model_name` and `provider` (both partial, case-insensitive). Every "
        "model returned here can be used as `ai_model` in POST /agents — "
        "`custom-llm` is excluded since it can't be used to create an agent "
        "through this API."
    ),
)
async def list_ai_models_public(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    model_name: Optional[str] = Query(None, description="Filter by partial model name (case-insensitive)"),
    provider: Optional[str] = Query(None, description="Filter by partial provider name (case-insensitive)"),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    skip = (page - 1) * size
    with db():
        query = db.session.query(AIModels).filter(AIModels.model_name != CUSTOM_LLM_MODEL_NAME)
        if model_name:
            query = query.filter(AIModels.model_name.ilike(f"%{model_name}%"))
        if provider:
            query = query.filter(AIModels.provider.ilike(f"%{provider}%"))
        total = query.count()
        models = query.order_by(AIModels.id.asc()).offset(skip).limit(size).all()
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=models,
        )
# -------------------------------------------------------------------
# KNOWLEDGE BASE
# -------------------------------------------------------------------

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt"}

def sync_agent_kb_logic(agent_id: int):
    """Internal helper for KB syncing with ElevenLabs"""
    try:
        with db():
            agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
            if not agent or not agent.elevenlabs_agent_id:
                return

            all_kb = (
                db.session.query(KnowledgeBaseModel)
                .join(AgentKnowledgeBaseBridge)
                .filter(AgentKnowledgeBaseBridge.agent_id == agent_id, KnowledgeBaseModel.elevenlabs_document_id.isnot(None))
                .all()
            )

            kb_docs = []
            for item in all_kb:
                doc_type = "file" if item.kb_type == "file" else "url" if item.kb_type == "url" else "text"
                kb_docs.append({
                    "id": item.elevenlabs_document_id,
                    "name": item.title or "Untitled",
                    "type": doc_type,
                    "usage_mode": "auto"
                })

            agent_client = ElevenLabsAgent()
            agent_client.update_agent(
                agent_id=agent.elevenlabs_agent_id,
                knowledge_base=kb_docs
            )
    except Exception as e:
        logger.error(f"Failed to sync KB for agent {agent_id}: {e}")

@router.get("/kb", response_model=PublicPaginatedResponse[KnowledgeBaseResponse], include_in_schema=False)
async def list_kb_public(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    skip = (page - 1) * size
    with db():
        query = db.session.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.user_id == current_user.id)
        total = query.count()
        kb_items = query.order_by(KnowledgeBaseModel.id.asc()).offset(skip).limit(size).all()
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=kb_items,
        )

@router.get("/kb/{id}", response_model=KnowledgeBaseResponse, include_in_schema=False)
async def get_kb_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        kb_item = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id == id, KnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_item:
            raise HTTPException(status_code=404, detail="Knowledge Base item not found")
        return kb_item

@router.post("/kb/url", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_kb_url_public(
    request: KnowledgeBaseURLCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    url_str = str(request.url)
    with db():
        existing_url = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.user_id == current_user.id,
            KnowledgeBaseModel.kb_type == "url",
            func.lower(KnowledgeBaseModel.content_path) == url_str.lower()
        ).first()
        if existing_url:
            raise HTTPException(
                status_code=400,
                detail="This URL has already been added to your knowledge base."
            )

        # Validate the URL is actually reachable before bothering ElevenLabs.
        title = scrape_webpage_title(url_str)

        kb_client = ElevenLabsKB()
        kb_response = kb_client.add_url_document(url_str)
        if not kb_response.status:
            logger.error(f"ElevenLabs KB URL addition failed: {kb_response.error_message}")
            raise HTTPException(status_code=424, detail=describe_kb_sync_error(kb_response.error_message))

        doc_id = kb_response.data.get("document_id")
        rag_id = kb_client.compute_rag_index(doc_id)

        kb_entry = KnowledgeBaseModel(
            user_id=current_user.id,
            kb_type="url",
            content_path=url_str,
            elevenlabs_document_id=doc_id,
            rag_index_id=rag_id,
            title=title
        )
        db.session.add(kb_entry)
        db.session.commit()
        db.session.refresh(kb_entry)
        return kb_entry

@router.post("/kb/text", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_kb_text_public(
    request: KnowledgeBaseTextCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        existing_text = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.user_id == current_user.id,
            KnowledgeBaseModel.kb_type == "text",
            KnowledgeBaseModel.title == request.title
        ).first()
        if existing_text:
            raise HTTPException(
                status_code=400,
                detail="This exact text content has already been added to your knowledge base."
            )

        kb_client = ElevenLabsKB()
        kb_response = kb_client.add_text_document(request.content, request.title)
        if not kb_response.status:
            raise HTTPException(status_code=424, detail=f"Knowledge base sync failure: {kb_response.error_message}")
        
        doc_id = kb_response.data.get("document_id")
        rag_id = kb_client.compute_rag_index(doc_id)

        kb_entry = KnowledgeBaseModel(
            user_id=current_user.id,
            kb_type="text",
            title=request.title,
            content_text=request.content,
            elevenlabs_document_id=doc_id,
            rag_index_id=rag_id
        )
        db.session.add(kb_entry)
        db.session.commit()
        db.session.refresh(kb_entry)
        return kb_entry

@router.post("/kb/file", response_model=List[KnowledgeBaseResponse], status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_kb_file_public(
    files: List[UploadFile] = File(...),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    responses = []
    
    with db():
        kb_client = ElevenLabsKB()
        seen_filenames = set()
        for file in files:
            _, ext = os.path.splitext(file.filename)
            if ext.lower() not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

            filename_key = file.filename.lower()
            if filename_key in seen_filenames:
                raise HTTPException(status_code=400, detail=f"Duplicate file name '{file.filename}' in this upload request.")
            seen_filenames.add(filename_key)

            existing_file = db.session.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.user_id == current_user.id,
                KnowledgeBaseModel.kb_type == "file",
                func.lower(KnowledgeBaseModel.title) == filename_key
            ).first()
            if existing_file:
                raise HTTPException(
                    status_code=400,
                    detail=f"A file named '{file.filename}' already exists in your knowledge base."
                )

            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds 10MB limit")

            file_path = os.path.join(UPLOAD_DIR, f"pub_{current_user.id}_{datetime.now(timezone.utc).timestamp()}_{file.filename}")
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            kb_response = kb_client.upload_document(file_path, name=file.filename)
            if not kb_response.status:
                if os.path.exists(file_path): os.remove(file_path)
                raise HTTPException(status_code=424, detail=f"Knowledge base sync failure for {file.filename}: {kb_response.error_message}")
            
            doc_id = kb_response.data.get("document_id")
            rag_id = kb_client.compute_rag_index(doc_id)

            kb_entry = KnowledgeBaseModel(
                user_id=current_user.id,
                kb_type="file",
                title=file.filename,
                content_path=file_path,
                elevenlabs_document_id=doc_id,
                rag_index_id=rag_id,
                file_size=round((file_size / (1024*1024)), 2)
            )
            db.session.add(kb_entry)
            db.session.flush()
            
            # Reattach to session strictly just to be sure
            responses.append(kb_entry)

        db.session.commit()
        for entry in responses:
            db.session.refresh(entry)
            
        return responses

@router.delete("/kb/{id}", include_in_schema=False)
async def delete_kb_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        kb_entry = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id == id, KnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_entry:
            raise HTTPException(status_code=404, detail="Knowledge Base item not found")
        
        bridges = db.session.query(AgentKnowledgeBaseBridge).filter(AgentKnowledgeBaseBridge.kb_id == id).all()
        agent_ids = [b.agent_id for b in bridges]

        if kb_entry.elevenlabs_document_id:
            try:
                ElevenLabsKB().delete_document(kb_entry.elevenlabs_document_id)
            except: pass

        if kb_entry.kb_type == "file" and kb_entry.content_path and os.path.exists(kb_entry.content_path):
            try: os.remove(kb_entry.content_path)
            except: pass

        for bridge in bridges: db.session.delete(bridge)
        db.session.delete(kb_entry)
        db.session.commit()

        for agent_id in agent_ids: sync_agent_kb_logic(agent_id)
    return None

@router.post("/kb/bind", status_code=status.HTTP_200_OK, include_in_schema=False)
async def bind_kb_public(
    request: KnowledgeBaseBind,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == request.agent_id, AgentModel.user_id == current_user.id
        ).first()
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        
        kb_entry = db.session.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.id == request.kb_id, KnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_entry: raise HTTPException(status_code=404, detail="Knowledge Base item not found")
        
        existing = db.session.query(AgentKnowledgeBaseBridge).filter(
            AgentKnowledgeBaseBridge.agent_id == request.agent_id, AgentKnowledgeBaseBridge.kb_id == request.kb_id
        ).first()
        
        if not existing:
            db.session.add(AgentKnowledgeBaseBridge(agent_id=request.agent_id, kb_id=request.kb_id))
            db.session.commit()
            sync_agent_kb_logic(request.agent_id)

    return {"message": "Knowledge base bound successfully"}

# -------------------------------------------------------------------
# PERSONAL KNOWLEDGE BASE (pgvector/FAISS-backed, independent of ElevenLabs)
#
# Items are attached to agents many-to-many via
# PersonalKnowledgeBaseAgentBridgeModel — see app_v2/routers/
# personal_knowledge_base.py for the attach/detach endpoints. An agent only
# gets its own search_personal_knowledge_base tool + prompt block once it has
# at least one item attached (app_v2/utils/personal_kb_tool.py).
# -------------------------------------------------------------------

@router.get("/personal-kb", response_model=PublicPaginatedResponse[PersonalKnowledgeBaseResponse])
async def list_personal_kb_public(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    title: str = None,
    kb_type: str = None,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        query = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.user_id == current_user.id
        )
        if title:
            query = query.filter(PersonalKnowledgeBaseModel.title.ilike(f"%{title}%"))
        if kb_type:
            query = query.filter(PersonalKnowledgeBaseModel.kb_type == kb_type)

        total = query.count()
        skip = (page - 1) * size
        items = query.order_by(PersonalKnowledgeBaseModel.id.asc()).offset(skip).limit(size).all()
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1,
            items=[_personal_kb_to_read(item) for item in items],
        )


@router.get("/personal-kb/{id}", response_model=PersonalKnowledgeBaseResponse)
async def get_personal_kb_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.id == id, PersonalKnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_entry:
            raise HTTPException(status_code=404, detail="Knowledge Base item not found")
        return _personal_kb_to_read(kb_entry)


@router.post("/personal-kb/url", response_model=PersonalKnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_personal_kb_url_public(
    request: PersonalKnowledgeBaseURLCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    url_str = str(request.url)
    with db():
        existing = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.user_id == current_user.id,
            PersonalKnowledgeBaseModel.kb_type == "url",
            func.lower(PersonalKnowledgeBaseModel.content_path) == url_str.lower(),
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="This URL has already been added to your knowledge base.")

        title, text = scrape_url(url_str)
        kb_entry = _store_personal_kb_entry(user_id=current_user.id, kb_type="url", title=title, text=text, content_path=url_str)
        result = _personal_kb_to_read(kb_entry)

    return result


@router.post("/personal-kb/text", response_model=PersonalKnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_personal_kb_text_public(
    request: PersonalKnowledgeBaseTextCreate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    with db():
        existing = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.user_id == current_user.id,
            PersonalKnowledgeBaseModel.kb_type == "text",
            PersonalKnowledgeBaseModel.title == request.title,
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="This exact text content has already been added to your knowledge base.")

        kb_entry = _store_personal_kb_entry(
            user_id=current_user.id, kb_type="text", title=request.title, text=request.content,
            embed_text=f"{request.title}\n\n{request.content}",
        )
        result = _personal_kb_to_read(kb_entry)

    return result


@router.post("/personal-kb/file", response_model=List[PersonalKnowledgeBaseResponse], status_code=status.HTTP_201_CREATED)
async def create_personal_kb_file_public(
    files: List[UploadFile] = File(...),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    with db():
        uploaded_entries = []
        seen_filenames = set()
        for file in files:
            _, ext = os.path.splitext(file.filename)
            if ext.lower() not in PERSONAL_KB_ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

            filename_key = file.filename.lower()
            if filename_key in seen_filenames:
                raise HTTPException(status_code=400, detail=f"Duplicate file name '{file.filename}' in this upload request.")
            seen_filenames.add(filename_key)

            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)
            if file_size == 0:
                raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")
            if file_size > PERSONAL_KB_MAX_FILE_SIZE_IN_MB * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds {PERSONAL_KB_MAX_FILE_SIZE_IN_MB}MB limit")

            file_path = os.path.join(PERSONAL_KB_UPLOAD_DIR, f"pub_{current_user.id}_{datetime.now(timezone.utc).timestamp()}_{file.filename}")
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            try:
                text = extract_text_from_file(file_path)
                kb_entry = _store_personal_kb_entry(
                    user_id=current_user.id, kb_type="file", title=file.filename, text=text,
                    content_path=file_path, file_size=round(file_size / 1024, 2),
                )
            except HTTPException:
                if os.path.exists(file_path):
                    os.remove(file_path)
                raise
            except Exception as e:
                if os.path.exists(file_path):
                    os.remove(file_path)
                logger.error(f"Error processing file '{file.filename}' for public personal KB: {e}")
                raise HTTPException(status_code=422, detail=f"Could not process file {file.filename}")

            uploaded_entries.append(kb_entry)

        result = [_personal_kb_to_read(entry) for entry in uploaded_entries]

    return result


@router.put("/personal-kb/{id}/url", response_model=PersonalKnowledgeBaseResponse)
async def update_personal_kb_url_public(
    id: int,
    request: PersonalKnowledgeBaseURLUpdate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    with db():
        kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.id == id,
            PersonalKnowledgeBaseModel.user_id == current_user.id,
            PersonalKnowledgeBaseModel.kb_type == "url",
        ).first()
        if not kb_entry:
            raise HTTPException(status_code=404, detail="URL Knowledge Base item not found")

        if request.url is not None:
            new_url = str(request.url)
            title, text = scrape_url(new_url)
            _replace_personal_kb_content(kb_entry, text)
            kb_entry.content_path = new_url
            kb_entry.title = request.title if request.title is not None else title
        elif request.title is not None:
            kb_entry.title = request.title

        db.session.commit()
        db.session.refresh(kb_entry)
        return _personal_kb_to_read(kb_entry)


@router.put("/personal-kb/{id}/text", response_model=PersonalKnowledgeBaseResponse)
async def update_personal_kb_text_public(
    id: int,
    request: PersonalKnowledgeBaseTextUpdate,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    with db():
        kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.id == id,
            PersonalKnowledgeBaseModel.user_id == current_user.id,
            PersonalKnowledgeBaseModel.kb_type == "text",
        ).first()
        if not kb_entry:
            raise HTTPException(status_code=404, detail="Text Knowledge Base item not found")

        if request.content is not None:
            new_title = request.title if request.title is not None else kb_entry.title
            _replace_personal_kb_content(kb_entry, request.content, embed_text=f"{new_title}\n\n{request.content}")
        if request.title is not None:
            kb_entry.title = request.title

        db.session.commit()
        db.session.refresh(kb_entry)
        return _personal_kb_to_read(kb_entry)


@router.put("/personal-kb/{id}/file", response_model=PersonalKnowledgeBaseResponse)
async def update_personal_kb_file_public(
    id: int,
    title: str = Form(None),
    file: UploadFile = File(None),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access, allow_coin_fallback=True))
):
    track_and_limit_api(current_user.id)
    with db():
        kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.id == id,
            PersonalKnowledgeBaseModel.user_id == current_user.id,
            PersonalKnowledgeBaseModel.kb_type == "file",
        ).first()
        if not kb_entry:
            raise HTTPException(status_code=404, detail="File Knowledge Base item not found")

        if file is not None:
            _, ext = os.path.splitext(file.filename)
            if ext.lower() not in PERSONAL_KB_ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Allowed: .docx, .pdf, .txt")

            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)
            if file_size == 0:
                raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")
            if file_size > PERSONAL_KB_MAX_FILE_SIZE_IN_MB * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds {PERSONAL_KB_MAX_FILE_SIZE_IN_MB}MB limit")

            new_file_path = os.path.join(PERSONAL_KB_UPLOAD_DIR, f"pub_{current_user.id}_{datetime.now(timezone.utc).timestamp()}_{file.filename}")
            with open(new_file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            try:
                text = extract_text_from_file(new_file_path)
                _replace_personal_kb_content(kb_entry, text)
            except HTTPException:
                if os.path.exists(new_file_path):
                    os.remove(new_file_path)
                raise
            except Exception as e:
                if os.path.exists(new_file_path):
                    os.remove(new_file_path)
                logger.error(f"Error processing file update for personal kb {id}: {e}")
                raise HTTPException(status_code=422, detail="Could not process file")

            old_path = kb_entry.content_path
            kb_entry.content_path = new_file_path
            kb_entry.file_size = round(file_size / 1024, 2)
            kb_entry.title = title if title is not None else file.filename
            if old_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        elif title is not None:
            kb_entry.title = title

        db.session.commit()
        db.session.refresh(kb_entry)
        return _personal_kb_to_read(kb_entry)


@router.delete("/personal-kb/{id}")
async def delete_personal_kb_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        kb_entry = db.session.query(PersonalKnowledgeBaseModel).filter(
            PersonalKnowledgeBaseModel.id == id, PersonalKnowledgeBaseModel.user_id == current_user.id
        ).first()
        if not kb_entry:
            raise HTTPException(status_code=404, detail="Knowledge Base item not found")

        chunk_ids = [
            row.id for row in db.session.query(PersonalKnowledgeBaseChunkModel.id)
            .filter(PersonalKnowledgeBaseChunkModel.kb_id == kb_entry.id).all()
        ]
        attached_agent_ids = [
            row.agent_id for row in db.session.query(PersonalKnowledgeBaseAgentBridgeModel.agent_id)
            .filter(PersonalKnowledgeBaseAgentBridgeModel.kb_id == kb_entry.id).all()
        ]

        if kb_entry.kb_type == "file" and kb_entry.content_path and os.path.exists(kb_entry.content_path):
            try:
                os.remove(kb_entry.content_path)
            except OSError:
                pass

        db.session.delete(kb_entry)  # cascades chunk rows + agent bridge rows
        db.session.commit()

    try:
        remove_embeddings(current_user.id, chunk_ids)
    except Exception as e:
        logger.warning(f"Failed to remove FAISS vectors for deleted public KB item {id}: {e}")

    for agent_id in attached_agent_ids:
        try:
            remove_personal_kb_tool_from_agent_if_empty(agent_id)
        except Exception as e:
            logger.warning(f"Failed to sync personal KB tool removal for agent {agent_id}: {e}")
    return None

@router.get("/ai-models/{id}", response_model=AIModelRead)
async def get_ai_model_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        model = db.session.query(AIModels).filter(
            AIModels.id == id,
            AIModels.model_name != CUSTOM_LLM_MODEL_NAME,
        ).first()
        if not model:
            raise HTTPException(status_code=404, detail="AI Model not found")
        return model

# -------------------------------------------------------------------
# FUNCTIONS (TOOLS) CRUD
# -------------------------------------------------------------------

# -------------------------------------------------------------------
# FUNCTIONS (TOOLS) CRUD
# -------------------------------------------------------------------

SENSITIVE_HEADER_KEYS = {"authorization", "x-api-key", "api-key", "token"}

def function_to_read(f: FunctionModel) -> FunctionRead:
    db_config = f.api_endpoint_url
    if not db_config:
        raise HTTPException(status_code=500, detail=f"Function '{f.name}' has no API config")

    decrypted_headers = {}
    for k, v in (db_config.headers or {}).items():
        if k.lower() in SENSITIVE_HEADER_KEYS:
            try:
                decrypted_headers[k] = decrypt_data(v)
            except Exception:
                decrypted_headers[k] = v
        else:
            decrypted_headers[k] = v

    return FunctionRead(
        id=f.id,
        name=f.name,
        description=f.description,
        elevenlabs_tool_id=f.elevenlabs_tool_id,
        created_at=f.created_at,
        modified_at=f.modified_at,
        api_config=ApiSchema(
            url=db_config.endpoint_url,
            method=db_config.http_method,
            request_headers=decrypted_headers,
            path_params_schema={k: PrimitiveField(**v) for k, v in db_config.path_params.items()} if db_config.path_params else None,
            query_params_schema=db_config.query_params if db_config.query_params else None,
            request_body_schema=db_config.body_schema if db_config.body_schema else None,
            response_variables=db_config.response_variables if db_config.response_variables else None,
            content_type="application/json" if db_config.body_schema else None,
        )
    )


@router.get("/functions", response_model=PublicPaginatedResponse[FunctionRead])
async def list_functions_public(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    skip = (page - 1) * size
    with db():
        query = db.session.query(FunctionModel).filter(FunctionModel.user_id == current_user.id)
        total = query.count()
        functions = (
            query
            .options(selectinload(FunctionModel.api_endpoint_url))
            .order_by(FunctionModel.created_at.desc(), FunctionModel.id.desc())
            .offset(skip).limit(size).all()
        )
        items = [function_to_read(f) for f in functions]
        total_pages = math.ceil(total / size) if total else 0
        return PublicPaginatedResponse(
            total=total, current_page=page, size=size, total_pages=total_pages,
            has_next=page < total_pages, has_previous=page > 1, items=items,
        )


@router.get("/functions/{id}", response_model=FunctionRead)
async def get_function_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        function = (
            db.session.query(FunctionModel)
            .options(selectinload(FunctionModel.api_endpoint_url))
            .filter(FunctionModel.id == id, FunctionModel.user_id == current_user.id)
            .first()
        )
        if not function:
            raise HTTPException(status_code=404, detail="Function not found")
        return function_to_read(function)


@router.post("/functions", response_model=FunctionRead, status_code=status.HTTP_201_CREATED)
async def create_function_public(
    function_in: FunctionCreateSchema,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    user_id = current_user.id

    with db():
        existing = db.session.query(FunctionModel).filter(
            FunctionModel.name == function_in.name,
            FunctionModel.user_id == user_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Function with name '{function_in.name}' already exists")

        el_client = ElevenLabsAgent()
        el_response = el_client.create_tool(
            name=function_in.name,
            description=function_in.description,
            api_schema=function_in.api_config
        )
        if not el_response.status:
            raise HTTPException(status_code=424, detail=f"Failed to create tool: {el_response.error_message}")

        elevenlabs_tool_id = el_response.data.get("id")

        try:
            new_function = FunctionModel(
                name=function_in.name,
                description=function_in.description,
                user_id=user_id,
                elevenlabs_tool_id=elevenlabs_tool_id
            )
            db.session.add(new_function)
            db.session.flush()

            headers = function_in.api_config.request_headers or {}
            encrypted_headers = {
                k: (encrypt_data(v) if k.lower() in SENSITIVE_HEADER_KEYS else v)
                for k, v in headers.items()
            }

            api_config = FunctionApiConfig(
                function_id=new_function.id,
                endpoint_url=function_in.api_config.url,
                http_method=function_in.api_config.method,
                headers=encrypted_headers,
                path_params={k: v.model_dump(exclude_none=True) for k, v in function_in.api_config.path_params_schema.items()} if function_in.api_config.path_params_schema else None,
                query_params=function_in.api_config.query_params_schema.model_dump(exclude_none=True) if function_in.api_config.query_params_schema else None,
                body_schema=function_in.api_config.request_body_schema.model_dump() if function_in.api_config.request_body_schema else None,
                response_variables=function_in.api_config.response_variables,
                timeout_ms=30000,
                speak_while_execution=False,
                speak_after_execution=True
            )
            db.session.add(api_config)
            db.session.commit()

            # re-fetch with api_endpoint_url eagerly loaded
            new_function = (
                db.session.query(FunctionModel)
                .options(selectinload(FunctionModel.api_endpoint_url))
                .filter(FunctionModel.id == new_function.id)
                .first()
            )
            return function_to_read(new_function)
        except HTTPException:
            raise
        except Exception as e:
            db.session.rollback()
            if elevenlabs_tool_id:
                el_client.delete_tool(elevenlabs_tool_id)
            raise HTTPException(status_code=500, detail=str(e))


@router.patch("/functions/{id}", response_model=FunctionRead)
async def update_function_public(
    id: int,
    function_in: FunctionUpdateSchema,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        function = (
            db.session.query(FunctionModel)
            .options(selectinload(FunctionModel.api_endpoint_url))
            .filter(FunctionModel.id == id, FunctionModel.user_id == current_user.id)
            .first()
        )
        if not function:
            raise HTTPException(status_code=404, detail="Function not found")

        el_update = False
        el_params = {}

        if function_in.name is not None:
            function.name = function_in.name
            el_params["name"] = function_in.name
            el_update = True

        if function_in.description is not None:
            function.description = function_in.description
            el_params["description"] = function_in.description
            el_update = True

        if function_in.api_config is not None or function_in.response_variables is not None:
            api_config = function.api_endpoint_url
            if not api_config:
                api_config = FunctionApiConfig(function_id=id)
                db.session.add(api_config)

            if function_in.response_variables is not None:
                api_config.response_variables = function_in.response_variables

            if function_in.api_config is not None:
                if function_in.api_config.url is not None:
                    api_config.endpoint_url = function_in.api_config.url
                if function_in.api_config.method is not None:
                    api_config.http_method = function_in.api_config.method
                if function_in.api_config.request_headers is not None:
                    api_config.headers = {
                        k: (encrypt_data(v) if k.lower() in SENSITIVE_HEADER_KEYS else v)
                        for k, v in function_in.api_config.request_headers.items()
                    }
                if function_in.api_config.path_params_schema is not None:
                    api_config.path_params = {k: v.model_dump(exclude_none=True) for k, v in function_in.api_config.path_params_schema.items()}
                if function_in.api_config.query_params_schema is not None:
                    api_config.query_params = function_in.api_config.query_params_schema.model_dump(exclude_none=True)
                if function_in.api_config.request_body_schema is not None:
                    api_config.body_schema = function_in.api_config.request_body_schema.model_dump()
                if function_in.api_config.response_variables is not None:
                    api_config.response_variables = function_in.api_config.response_variables

            decrypted_headers = {}
            for k, v in (api_config.headers or {}).items():
                if k.lower() in SENSITIVE_HEADER_KEYS:
                    try:
                        decrypted_headers[k] = decrypt_data(v)
                    except Exception:
                        decrypted_headers[k] = v
                else:
                    decrypted_headers[k] = v

            el_params["api_schema"] = ApiSchema(
                url=api_config.endpoint_url,
                method=api_config.http_method,
                request_headers=decrypted_headers,
                path_params_schema={k: PrimitiveField(**v) for k, v in api_config.path_params.items()} if api_config.path_params else None,
                query_params_schema=api_config.query_params if api_config.query_params else None,
                request_body_schema=api_config.body_schema if api_config.body_schema else None,
                response_variables=api_config.response_variables,
                content_type="application/json" if api_config.body_schema else None,
            )
            el_update = True

        if el_update and function.elevenlabs_tool_id:
            el_client = ElevenLabsAgent()
            el_res = el_client.update_tool(tool_id=function.elevenlabs_tool_id, **el_params)
            if not el_res.status:
                db.session.rollback()
                raise HTTPException(status_code=424, detail=f"Failed to update tool: {el_res.error_message}")

        db.session.commit()
        db.session.refresh(function)
        return function_to_read(function)


@router.delete("/functions/{id}")
async def delete_function_public(
    id: int,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    with db():
        function = db.session.query(FunctionModel).filter(
            FunctionModel.id == id, FunctionModel.user_id == current_user.id
        ).first()
        if not function:
            raise HTTPException(status_code=404, detail="Function not found")

        if function.elevenlabs_tool_id:
            try:
                ElevenLabsAgent().delete_tool(function.elevenlabs_tool_id)
            except Exception:
                pass

        db.session.delete(function)
        db.session.commit()
    return None

@router.post("/functions/bind", status_code=status.HTTP_200_OK)
async def bind_function_public(
    request: FunctionBind,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    agent_id = request.agent_id
    function_id = request.function_id
    with db():
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.user_id == current_user.id).first()
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        
        function = db.session.query(FunctionModel).filter(FunctionModel.id == function_id, FunctionModel.user_id == current_user.id).first()
        if not function: raise HTTPException(status_code=404, detail="Function not found")
        
        existing = db.session.query(AgentFunctionBridgeModel).filter(
            AgentFunctionBridgeModel.agent_id == agent_id, AgentFunctionBridgeModel.function_id == function_id
        ).first()
        
        if not existing:
            db.session.add(AgentFunctionBridgeModel(agent_id=agent_id, function_id=function_id))
            db.session.commit()
            # ElevenLabs Sync
            if agent.elevenlabs_agent_id:
                bridges = db.session.query(AgentFunctionBridgeModel).filter(AgentFunctionBridgeModel.agent_id == agent_id).all()
                tool_ids = [b.function.elevenlabs_tool_id for b in bridges if b.function.elevenlabs_tool_id]
                ElevenLabsAgent().update_agent(agent_id=agent.elevenlabs_agent_id, tool_ids=tool_ids)
                
    return {"message": "Function bound successfully"}

@router.post("/functions/unbind", status_code=status.HTTP_200_OK)
async def unbind_function_public(
    request: FunctionUnbind,
    current_user: UnifiedAuthModel = Depends(RequireFeaturePublic(PlanFeatureEnum.api_access))
):
    track_and_limit_api(current_user.id)
    agent_id = request.agent_id
    function_id = request.function_id
    with db():
        agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id, AgentModel.user_id == current_user.id).first()
        if not agent: raise HTTPException(status_code=404, detail="Agent not found")
        
        bridge = db.session.query(AgentFunctionBridgeModel).filter(
            AgentFunctionBridgeModel.agent_id == agent_id, AgentFunctionBridgeModel.function_id == function_id
        ).first()
        
        if bridge:
            db.session.delete(bridge)
            db.session.commit()
            # ElevenLabs Sync
            if agent.elevenlabs_agent_id:
                bridges = db.session.query(AgentFunctionBridgeModel).filter(AgentFunctionBridgeModel.agent_id == agent_id).all()
                tool_ids = [b.function.elevenlabs_tool_id for b in bridges if b.function.elevenlabs_tool_id]
                ElevenLabsAgent().update_agent(agent_id=agent.elevenlabs_agent_id, tool_ids=tool_ids)
                
    return {"message": "Function unbound successfully"}

