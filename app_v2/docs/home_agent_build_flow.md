# Home "Build an Agent" Flow - Voice Ninja

## Overview

The Home page (`voice-ninja-client/src/app/(dashboard)/home/`) lets a user
describe an agent in one sentence, optionally attach files/links it should
learn from, and get a fully-configured, ElevenLabs-backed `AgentModel` back
without touching the full Agent Builder form. It's an async job flow: the
POST returns immediately with a job id, and the client polls for status
until the pipeline finishes (or fails).

```
POST /api/v2/agent-build/          -> 202, { id, status, error_message: null, agent: null }
GET  /api/v2/agent-build/{job_id}  -> 200, { id, status, error_message, agent }
```

Router: `app_v2/routers/agent_build.py`
Pipeline (runs in a daemon thread): `app_v2/utils/agent_build_pipeline.py`
Job table: `AgentBuildJobModel` (`app_v2/databases/models.py`)

## Step 1: Start a build (`POST /api/v2/agent-build/`)

Sent as `multipart/form-data` (not JSON) so file uploads can travel in the
same request as the text fields — see `agent_build.py:97-143`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `requirement` | string | yes | 10-2000 chars after trim (`agent_build_schema.py`'s `validate_requirement_text`) |
| `urls` | string, repeated | no | up to `MAX_KNOWLEDGE_URLS` (5); each must start with `http://`/`https://` |
| `files` | file, repeated | no | up to `MAX_KNOWLEDGE_FILES` (5); `.pdf`/`.docx`/`.txt` only, same size cap as the personal KB flow (`VoiceSettings.MAX_FILE_UPLOAD_MB`) |

Validation order (`agent_build.py:_clean_urls` / `_save_knowledge_files`):
1. `requirement` length — 400 on failure.
2. URL scheme + count — 400 on failure.
3. File extension, emptiness, size, count — 400 on failure; any rejection
   deletes the just-created job row so no orphan job is left behind.

On success, the job row is created with `knowledge_urls`/`knowledge_files`
populated (files already saved to disk under
`app_v2/routers/personal_knowledge_base.py`'s `UPLOAD_DIR`), then a daemon
thread runs `run_agent_build_job(job_id, user_id)` and the endpoint returns
`202` immediately with the job's initial status.

## Step 2: Poll for status (`GET /api/v2/agent-build/{job_id}`)

Returns 404 if the job doesn't exist or belongs to a different user.
`agent` stays `null` until `status` is `"completed"`.

## Step 3: The pipeline (`run_agent_build_job`)

Runs entirely inside a single `with db():` thread-local session. Each stage
updates `job.status` (visible to the poller) before doing its work:

1. `understanding_requirement`
2. `generating_conversation` — `generate_system_prompt_from_instructions_async(requirement)` (LLM call)
3. `configuring_agent` — resolves the default AI model + language
4. `configuring_knowledge` — **`_ingest_knowledge_attachments(job)`**:
   - each `knowledge_files` entry: `extract_text_from_file` → `_store_kb_entry(kb_type="file", ...)`
   - each `knowledge_urls` entry: `scrape_url` → `_store_kb_entry(kb_type="url", ...)`
   - both reuse the personal-KB store (`app_v2/routers/personal_knowledge_base.py`) — chunked, embedded, and written to the user's FAISS index exactly like a manual Knowledge Base upload.
   - **non-fatal per item**: a bad file/URL is logged and skipped, not raised — knowledge ingestion never blocks the agent itself from being created.
5. `configuring_voice` — resolves the default voice
6. `creating_voice_agent` — `create_agent_core(...)` creates the real ElevenLabs agent + `AgentModel` row
7. `finalizing` — `job.agent_id` is set
8. **`_attach_knowledge_to_agent(kb_entry_ids, new_agent.id)`** — binds every KB entry ingested in step 4 to the new agent via `PersonalKnowledgeBaseAgentBridgeModel`, then calls `ensure_personal_kb_tool_for_agent` so the agent gets its `search_personal_knowledge_base` tool/prompt block. Also non-fatal — a binding failure is logged, not raised, since the agent has already been created successfully by this point.
9. `completed` (or `failed` with a user-safe `error_message` — see `_friendly_error_message`)

### Why ingestion is non-fatal

The whole point of the Home flow is "describe it, get a working agent
fast." An unreachable URL or a corrupt PDF attached alongside a perfectly
good requirement shouldn't turn a successful build into a failed one — the
user still gets their agent, just without that one attachment's knowledge.

## Frontend

- `voice-ninja-client/src/app/(dashboard)/home/hooks/useAgentBuild.ts` — owns `requirement`/`urls`/`files` state and the idle → submitting → building → ready/error state machine.
- `voice-ninja-client/src/app/(dashboard)/home/components/RequirementComposer.tsx` — the composer UI: mic dictation (browser `SpeechRecognition` API, client-side only — no STT backend call), "Upload files" / "Add a link" buttons, and the example-prompt pills.
- `voice-ninja-client/src/services/user/agentBuild.ts` — builds the `FormData` payload (`requirement` + repeated `urls`/`files` fields) for the multipart POST.
