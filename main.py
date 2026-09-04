import os
from dotenv import load_dotenv
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
# torch (embeddings) and faiss (vector index) each bring their own OpenMP
# runtime; sharing a process crashes (segfault, sometimes mid-request, always
# on process exit) unless both are pinned to a single thread. Must be set
# before either library is imported anywhere in the app — our per-request
# workloads are tiny, so this costs nothing. See app_v2/utils/faiss_store.py
# and app_v2/utils/embedding_utils.py for the matching in-process pins.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Load environment variables
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi_sqlalchemy import DBSessionMiddleware, db
from app_v2.core.config import VoiceSettings
from starlette.middleware.sessions import SessionMiddleware
from app_v2.databases.models import AdminTokenModel, TokensToConsume, VoiceModel
from app_v2.core.exceptions import get_readable_message
from app_v2.routers import otp_router, health_router, google_auth_router, profile_router, lang_router, ai_model_router, agent_router, voice_router, function_router, knowledge_base_router, personal_knowledge_base_router, phone_router, widget_router,websocket_router,conversation_router,widget_config_router, user_dashboard_router,admin_dashboard_router, coin_purchase_router, admin_user_management, payment_insights_router, api_key_management, public_api,public_websocket_router,webhooks, twilio_connector_router, web_agent_config_router, web_agent_router, invoice_files, support_router, support_public_router, admin_support, sessions_router, internal_reconciliation, agent_build_router
from app_v2.routers.email_subscription import public_router as email_subscription_public_router, admin_router as email_subscription_admin_router
from app_v2.utils.jwt_utils import HTTPBearer
from fastapi.responses import HTMLResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from pathlib import Path
from fastapi.staticfiles import StaticFiles

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Kicks off loading the personal-KB embedding model in the background on
    # startup, so it's warm by the time the first real request needs it —
    # without blocking server startup itself on the (multi-second) load. Any
    # request that needs the model before this finishes just waits on the
    # same load instead of triggering a duplicate one (see the lock in
    # embedding_utils.get_embeddings()).
    import threading
    from app_v2.utils.embedding_utils import get_embeddings
    from app_v2.core.logger import setup_logger
    logger = setup_logger(__name__)

    def _preload():
        try:
            get_embeddings()
        except Exception as e:
            logger.warning(f"Failed to preload embedding model in background, will load lazily on first use: {e}")

    threading.Thread(target=_preload, daemon=True, name="preload-embedding-model").start()
    yield


app = FastAPI(title="Voice Ninja V2 API", version="2.0.0",docs_url=None,
    redoc_url=None, lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent

app.mount("/static", StaticFiles(directory=BASE_DIR / "app_v2"/"static"), name="static")

# Serves personal-KB (and other) uploaded files directly by their on-disk
# relative path (e.g. "uploads/personal_kb/pub_1_..._x.txt") so the public
# API can hand back a plain, clickable URL as `content_path` for file-type
# KB items (see _public_kb_to_read in app_v2/routers/public_api.py) instead
# of a server-local path meaningless to an external caller.
_uploads_dir = BASE_DIR / "uploads"
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")

@app.get("/docs", include_in_schema=False)
def custom_docs():
    html = open(BASE_DIR / "app_v2"/"templates" / "swagger.html").read()
    return HTMLResponse(html)





# Global exception handler for Pydantic validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()

    field_errors = []

    for err in errors:
        loc = err.get("loc", [])
        field = loc[-1] if loc else "field"
        msg = err.get("msg", "Invalid value")

        readable_msg = get_readable_message(field, msg)
        field_errors.append(readable_msg)

    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "message": "; ".join(field_errors),
                "status": "failed",
                "status_code": 400
            }
        }
    )

# Global exception handler to ensure consistent error response structure
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):

    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": {
                    "message": exc.detail.get("message", "Something went wrong"),
                    "status": exc.detail.get("status", "failed"),
                    "status_code": exc.status_code
                }
            }
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": {
                "message": str(exc.detail),
                "status": "failed",
                "status_code": exc.status_code
            }
        }
    )


def _is_public_api_path(path: str) -> bool:
    return path.startswith("/api/v2/public")


# Starlette raises its own (base-class) HTTPException for router-level errors
# - 404 for an unmatched path, 405 for a matched path/wrong method - straight
# from Router.handle(), before any individual route's `route_class` (e.g.
# PublicAPIRoute in app_v2/routers/public_api.py) gets a chance to run. Since
# that base class is never the exact type the `HTTPException` handler above is
# registered against (that's the fastapi.HTTPException subclass), those
# responses fell through to FastAPI's bare default `{"detail": ...}` handler
# instead of either envelope format. Catch it explicitly and route by path so
# public API callers still get `_public_envelope`.
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if _is_public_api_path(request.url.path):
        from app_v2.routers.public_api import _public_envelope
        message = str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_public_envelope("failed", message=message, detail=message),
            headers=getattr(exc, "headers", None),
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


# Last-resort net for anything that isn't an HTTPException/RequestValidationError
# and manages to escape PublicAPIRoute's own catch-all (app_v2/routers/public_api.py)
# - e.g. an error raised by ASGI middleware above routing. Without this, such an
# error would produce Starlette's bare-text 500, not JSON at all.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from app_v2.core.logger import setup_logger
    setup_logger(__name__).error(f"Unhandled error on {request.url.path}: {exc}", exc_info=True)
    if _is_public_api_path(request.url.path):
        from app_v2.routers.public_api import _public_envelope
        return JSONResponse(
            status_code=500,
            content=_public_envelope(
                "failed",
                message="Something went wrong. Please try again later.",
                detail=str(exc),
            ),
        )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "message": "Something went wrong. Please try again later.",
                "status": "failed",
                "status_code": 500,
            }
        },
    )

# Security scheme for Bearer token
security = HTTPBearer()

# Postman's OpenAPI importer handles 3.1-style schema shapes unreliably against
# a spec declared as 3.0.x:
#  - `anyOf: [{type: X}, {type: null}]` (how Pydantic v2/FastAPI represents
#    `Optional[X]` params) silently drops the parameter from the generated
#    request instead of showing it as an optional filter. Downgraded to the
#    3.0-style `{type: X, nullable: true}` shape, which Postman has always
#    understood. See app_v2/routers/public_api.py `list_agents` for the
#    endpoint this was originally reported against (filters missing on import).
#  - `const: X` (how a single-value `Literal[X]`, e.g. the `Accept` header,
#    is represented) isn't part of the OpenAPI-3.0-era JSON Schema dialect.
#    Downgraded to the equivalent `enum: [X]`, which Postman renders as a
#    fixed/single-choice value.
def _openapi_31_anyof_null_to_30_nullable(node):
    if isinstance(node, dict):
        if isinstance(node.get("anyOf"), list):
            members = node["anyOf"]
            non_null = [m for m in members if m != {"type": "null"}]
            if len(non_null) != len(members):
                if len(non_null) == 1:
                    target = non_null[0]
                    rest = {k: v for k, v in node.items() if k != "anyOf"}
                    if "$ref" in target:
                        rest["allOf"] = [target]
                    else:
                        rest.update(target)
                    rest["nullable"] = True
                    node.clear()
                    node.update(rest)
                else:
                    # More than one non-null branch (e.g. Optional[Union[bool,
                    # SomeModel]]) - OpenAPI 3.0's dialect has no `type: null`
                    # value at all, so simply dropping the null member and
                    # keeping `nullable: true` alongside anyOf (both are valid
                    # 3.0 Schema Object keywords) is enough; there's nothing
                    # to merge inline like the single-branch case above.
                    node["anyOf"] = non_null
                    node["nullable"] = True
        if "const" in node:
            node["enum"] = [node.pop("const")]
        for key, value in node.items():
            _openapi_31_anyof_null_to_30_nullable(value)
    elif isinstance(node, list):
        for item in node:
            _openapi_31_anyof_null_to_30_nullable(item)


# Pydantic v2's Field(examples=[...]) and model_config json_schema_extra
# with an "examples" list produce the JSON-Schema-2020-12-style `examples`
# array (OpenAPI 3.1) on the Schema Object. OpenAPI 3.0 Schema Objects only
# recognize a single `example` value — Postman's 3.0 importer silently
# ignores an `examples` array there, which is why fields/models given only
# `examples=` (e.g. PublicAgentCreate's request body example, `tools`,
# `knowledgebase`, `variables`) weren't populating any default value in
# imported request bodies. Downgrades by keeping the first entry.
def _openapi_31_examples_to_30_example(node):
    if isinstance(node, dict):
        if isinstance(node.get("examples"), list) and node["examples"]:
            node["example"] = node.pop("examples")[0]
        else:
            node.pop("examples", None)
        for value in node.values():
            _openapi_31_examples_to_30_example(value)
    elif isinstance(node, list):
        for item in node:
            _openapi_31_examples_to_30_example(item)


# Every response on /api/v2/public/* actually goes out wrapped in
# `_public_envelope` (see PublicAPIRoute.get_route_handler in
# app_v2/routers/public_api.py), not as the bare `response_model` FastAPI
# documents by default. Two corrections needed for the docs (and therefore
# for anything imported from them, e.g. a Postman collection) to match what
# callers actually receive:
#  - the auto-added 422 "Validation Error" response doesn't apply: a bad
#    request on these routes comes back as a 400 in the envelope shape, never
#    as a bare FastAPI 422, so that example is actively misleading here.
#  - the 2xx response schema needs the real payload nested under `data`
#    inside the envelope, instead of documented as the bare payload.
def _wrap_public_api_responses_in_envelope(openapi_schema):
    for path, path_item in openapi_schema.get("paths", {}).items():
        if not path.startswith("/api/v2/public"):
            continue
        for method, operation in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            responses = operation.get("responses", {})
            responses.pop("422", None)
            for code, resp in responses.items():
                if not code.startswith("2"):
                    continue
                content = resp.get("content", {}).get("application/json")
                if not content or "schema" not in content:
                    continue
                original_schema = content["schema"]
                content["schema"] = {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "example": "success"},
                        "data": original_schema,
                        "message": {"type": "string", "example": ""},
                        "detail": {"type": "string", "example": ""},
                    },
                }


# Gates the docs surface behind SHOW_ALL_APIS_IN_SWAGGER (see config.py):
# with it unset/false, /docs and /openapi.json (and therefore anything
# imported from that link into Postman) only list /api/v2/public/* routes —
# internal/admin/auth endpoints stay out of any externally-shared docs link.
def _restrict_openapi_to_public_v2(openapi_schema):
    paths = openapi_schema.get("paths", {})
    openapi_schema["paths"] = {
        path: item for path, item in paths.items() if path.startswith("/api/v2/public")
    }


def _collect_schema_refs(node, refs):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            refs.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            _collect_schema_refs(value, refs)
    elif isinstance(node, list):
        for item in node:
            _collect_schema_refs(item, refs)


# get_openapi(routes=app.routes) builds `components.schemas` for every model
# any route in the whole app uses, not just the (by then already
# path-filtered) public ones - so with SHOW_ALL_APIS_IN_SWAGGER off, the
# public docs/Postman collection were still carrying every internal/admin
# model's schema (e.g. CoinUsageSettings fields), just with no path left
# pointing at most of them. Recomputes the schema closure actually reachable
# from the remaining paths and drops the rest, so /docs and any Postman
# import only ever see models the public API can actually return or accept.
def _prune_unused_components(openapi_schema):
    schemas = openapi_schema.get("components", {}).get("schemas")
    if not schemas:
        return
    used = set()
    _collect_schema_refs(openapi_schema.get("paths", {}), used)
    frontier = set(used)
    while frontier:
        next_frontier = set()
        for name in frontier:
            found = set()
            _collect_schema_refs(schemas.get(name, {}), found)
            new_names = found - used
            used |= new_names
            next_frontier |= new_names
        frontier = next_frontier
    openapi_schema["components"]["schemas"] = {
        name: schema for name, schema in schemas.items() if name in used
    }


# Every public route was previously tagged just "public-api", so Swagger
# rendered the entire public surface as one flat, undifferentiated list.
# Regrouping by resource (longest-matching path prefix wins) gives each
# resource its own collapsible section - Agents, Widgets, etc - the same way
# a hand-maintained `tags=[...]` per route would, without having to touch
# every route decorator in public_api.py. Order here is also the section
# order Swagger renders, via the `tags` list built below.
_PUBLIC_API_TAG_PREFIXES = [
    ("/api/v2/public/agents", "Agents"),
    ("/api/v2/public/widgets", "Widgets"),
    ("/api/v2/public/web-agents", "Web Agents"),
    ("/api/v2/public/languages", "Languages"),
    ("/api/v2/public/voices", "Voices"),
    ("/api/v2/public/ai-models", "AI Models"),
    ("/api/v2/public/personal-kb", "Personal Knowledge Base"),
    ("/api/v2/public/functions", "Functions"),
    ("/api/v2/public/ws", "Public WebSocket"),
]

_PUBLIC_API_TAG_DESCRIPTIONS = {
    "Agents": "Create, configure, and manage your voice AI agents.",
    "Widgets": "Embeddable chat/voice widgets backed by an agent.",
    "Web Agents": "Standalone, hosted web pages for an agent.",
    "Languages": "Languages available to assign to an agent.",
    "Voices": "Voices available to assign to an agent.",
    "AI Models": "LLMs available to assign to an agent.",
    "Personal Knowledge Base": "Per-account knowledge base documents an agent can search, and binding them to agents.",
    "Functions": "Custom tools/functions an agent can call, and binding/unbinding them to agents.",
    "Public WebSocket": "Real-time voice conversations over a WebSocket connection.",
}


def _regroup_public_api_tags(openapi_schema):
    for path, path_item in openapi_schema.get("paths", {}).items():
        if not path.startswith("/api/v2/public"):
            continue
        tag = next((t for prefix, t in _PUBLIC_API_TAG_PREFIXES if path.startswith(prefix)), None)
        if not tag:
            continue
        for method, operation in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            operation["tags"] = [tag]
    openapi_schema["tags"] = [
        {"name": tag, "description": _PUBLIC_API_TAG_DESCRIPTIONS[tag]}
        for _, tag in _PUBLIC_API_TAG_PREFIXES
    ]


# FastAPI/Starlette never put websocket routes into the OpenAPI schema (only
# HTTP routes have a documentable request/response shape), so the public
# WebSocket endpoint (app_v2/routers/public_websocket_router.py) is otherwise
# invisible in both Swagger and any Postman collection generated from
# /openapi.json. There's no OpenAPI 3.0/3.1 operation type for "websocket",
# so this documents the handshake as a GET (which is what a WS upgrade
# actually is at the HTTP level) and uses the request/response bodies purely
# to carry example messages for every event type the connection exchanges -
# see app_v2/docs/public_websocket_api.md for the prose version of this.
def _public_websocket_path_item():
    # Client -> server, in the order they'd actually be sent over one call.
    client_examples = {
        "1_connect_auth": {
            "summary": "Step 1 - connect: authenticate (required first message, within 5s of connecting)",
            "value": {"type": "auth", "client_id": "YOUR_CLIENT_ID", "client_secret": "YOUR_CLIENT_SECRET"},
        },
        "2_stream_audio_note": {
            "summary": "Step 2 - stream: raw PCM 16kHz/mono/16-bit audio, sent as BINARY frames (not JSON - shown here only as a placeholder)",
            "value": {"note": "binary frame, not JSON - see the endpoint description"},
        },
        "3_optional_ping": {
            "summary": "Optional - keepalive/control message forwarded as-is to the voice engine",
            "value": {"type": "ping"},
        },
    }
    # Server -> client, grouped by where they show up in the call lifecycle.
    server_examples = {
        "1_connected_authenticated": {
            "summary": "Connect - auth succeeded, connection is now live",
            "value": {"type": "status", "message": "Authenticated successfully", "ts": "2024-01-01T10:00:00Z"},
        },
        "2_audio_interface_ready": {
            "summary": "Connect - agent's audio bridge is live, conversation_id assigned",
            "value": {
                "type": "status",
                "message": "Audio interface ready",
                "conversation_id": "conv_abc123",
                "ts": "2024-01-01T10:00:01Z",
            },
        },
        "3_audio_event": {
            "summary": "Stream - audio metadata accompanying each binary audio frame (audio itself is a separate binary frame, stripped here)",
            "value": {
                "type": "audio",
                "audio_event": {"audio_base_64": "[STRIPPED]", "event_id": 12},
            },
        },
        "4_user_transcript": {
            "summary": "Stream - live transcription of what the caller said",
            "value": {"type": "user_transcript", "text": "Hello, how are you?", "ts": "2024-01-01T10:00:05.123Z"},
        },
        "5_agent_response": {
            "summary": "Stream - live transcription of what the agent said",
            "value": {
                "type": "agent_response",
                "text": "I am doing well, thank you! How can I help you today?",
                "ts": "2024-01-01T10:00:07.456Z",
            },
        },
        "6_interruption": {
            "summary": "Stream - the caller barged in; audio at/before this event_id should be discarded client-side",
            "value": {"type": "interruption", "interruption_event": {"event_id": 42}},
        },
        "7_error_insufficient_coins": {
            "summary": "Error - not enough coin balance to start the call (closes with code 1008 right after)",
            "value": {"type": "error", "message": "Insufficient coins. Minimum 10 coins required to start a call.", "code": 1008},
        },
        "8_error_monthly_limit_mid_call": {
            "summary": "Error - monthly minutes limit hit mid-call (closes with code 1008 right after)",
            "value": {"type": "error", "message": "Monthly minutes limit reached. Call disconnected."},
        },
        "9_error_low_balance_mid_call": {
            "summary": "Error - coin balance ran out mid-call (closes with code 1008 right after)",
            "value": {"type": "error", "message": "Call ended due to low coins balance"},
        },
        "10_error_server_config": {
            "summary": "Error - server misconfiguration, e.g. missing API key (closes with code 1011 right after)",
            "value": {"type": "error", "message": "Server configuration error", "code": 1011},
        },
        "11_disconnect_call_ended": {
            "summary": "Disconnect - the agent ended the call normally",
            "value": {"type": "call_ended", "message": "The agent ended the call.", "ts": "2024-01-01T10:00:30Z"},
        },
    }
    description = (
        "**This is a WebSocket endpoint, not a plain HTTP GET.** Connect with "
        "`ws://` or `wss://` at this path - it's documented as a GET here (and "
        "therefore in any Postman collection imported from this spec) only "
        "because OpenAPI has no dedicated WebSocket operation type. To get a "
        "native WebSocket request in Postman (with the WS icon and a Messages "
        "tab instead of Body), create one manually via **New > WebSocket "
        "Request** pointed at this same URL, using the examples below for the "
        "messages you send/expect - Postman's spec importer cannot create that "
        "request type on its own, for any API, because OpenAPI has no concept "
        "of a WebSocket operation to import.\n\n"
        "### Connection lifecycle\n"
        "1. **Connect & authenticate** - open the socket, then immediately send "
        "the `auth` message (see the request examples) as the very first "
        "message, within 5 seconds, or the server closes the connection "
        "(`1008`, no JSON message sent first). On success you get back a "
        "`status: Authenticated successfully` message, followed shortly by "
        "`status: Audio interface ready` once the agent's audio bridge comes "
        "up.\n"
        "2. **Stream** - send raw PCM 16kHz mono 16-bit little-endian audio as "
        "binary frames (~100-200ms per chunk). Audio comes back the same way, "
        "interleaved with `audio`, `user_transcript`, `agent_response`, and "
        "`interruption` JSON events (see the response examples).\n"
        "3. **Errors** - a policy/limit violation sends an `error` JSON message "
        "(see the response examples for the exact wording of each case), then "
        "closes the socket - `1008` for balance/limit/auth problems, `1011` for "
        "server-side failures. A few close reasons (bad auth, agent not found) "
        "close the socket directly with no JSON message first.\n"
        "4. **Disconnect** - either side can end the call: the agent hanging up "
        "sends `call_ended` before closing; the client can simply close the "
        "socket at any time.\n\n"
        "### Close codes\n"
        "| Code | Meaning |\n"
        "| --- | --- |\n"
        "| `1008` | Policy violation - bad/missing auth, unknown agent, or a "
        "coin/usage limit was hit. |\n"
        "| `1011` | Internal error - server misconfiguration or a failure "
        "talking to the voice engine. |\n\n"
        "The request/response examples below cover every JSON message type "
        "this endpoint sends or accepts, in the order they occur."
    )
    return {
        "get": {
            "tags": ["Public WebSocket"],
            "summary": "Real-time voice conversation (WebSocket)",
            "description": description,
            "parameters": [
                {
                    "name": "agent_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                    "description": "ID of the agent to converse with.",
                }
            ],
            "requestBody": {
                "description": "Client-to-server JSON messages, sent over the WebSocket connection (audio itself is sent as binary frames, not JSON).",
                "content": {
                    "application/json": {
                        "schema": {"type": "object"},
                        "examples": client_examples,
                    }
                },
            },
            "responses": {
                "101": {
                    "description": (
                        "Server-to-client JSON messages, sent over the WebSocket connection. "
                        "Binary audio frames are also sent but aren't representable here."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                            "examples": server_examples,
                        }
                    },
                }
            },
        }
    }


def _inject_public_websocket_docs(openapi_schema):
    openapi_schema.setdefault("paths", {})["/api/v2/public/ws/{agent_id}"] = _public_websocket_path_item()


# Custom OpenAPI function to add security scheme
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    # Get the default OpenAPI schema
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version="3.0.3",
        description=app.description,
        routes=app.routes,
    )
    _openapi_31_anyof_null_to_30_nullable(openapi_schema)
    _openapi_31_examples_to_30_example(openapi_schema)
    _wrap_public_api_responses_in_envelope(openapi_schema)
    # Injected after the envelope wrap above (which only applies to real
    # response_model-backed routes) so its handwritten examples survive
    # untouched, but before the public-path restriction/tag regroup below so
    # it's gated and grouped exactly like every other public route.
    _inject_public_websocket_docs(openapi_schema)
    if not VoiceSettings.SHOW_ALL_APIS_IN_SWAGGER:
        _restrict_openapi_to_public_v2(openapi_schema)
        _prune_unused_components(openapi_schema)
    _regroup_public_api_tags(openapi_schema)
    # Add security scheme
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# Add security scheme to OpenAPI
app.openapi_components = {
    "securitySchemes": {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
}

# Add Middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    DBSessionMiddleware,
    db_url=VoiceSettings.DB_URL,
    engine_args={"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}
)

app.add_middleware(SessionMiddleware, secret_key=VoiceSettings.SECRET_KEY)

# Include app_v2 routers
app.include_router(otp_router)
app.include_router(health_router)
app.include_router(google_auth_router)
app.include_router(profile_router)

app.include_router(lang_router)
app.include_router(ai_model_router)
app.include_router(agent_router)
app.include_router(voice_router)
app.include_router(function_router)
app.include_router(knowledge_base_router, include_in_schema=False)
app.include_router(personal_knowledge_base_router)
app.include_router(phone_router)
app.include_router(widget_router)
app.include_router(websocket_router)
app.include_router(conversation_router)
app.include_router(widget_config_router)
app.include_router(user_dashboard_router)
app.include_router(admin_dashboard_router)
app.include_router(coin_purchase_router)
app.include_router(payment_insights_router)
app.include_router(admin_user_management.router)
app.include_router(api_key_management.router)
app.include_router(public_api.router)
app.include_router(public_websocket_router.router)
app.include_router(webhooks.router)
app.include_router(invoice_files.router)
app.include_router(twilio_connector_router)
app.include_router(web_agent_config_router)
app.include_router(web_agent_router)
app.include_router(email_subscription_public_router)
app.include_router(email_subscription_admin_router)
app.include_router(support_router)
app.include_router(support_public_router)
app.include_router(admin_support.router)
app.include_router(sessions_router)
app.include_router(internal_reconciliation.router, include_in_schema=False)
app.include_router(agent_build_router)


@app.get("/", tags=["System"])
async def root():
    return {"message": "Voice Ninja V2 API is running", "app_v2_status": "active"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

