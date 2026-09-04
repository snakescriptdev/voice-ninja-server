# Changelog — voice-ninja-server

## 2026-09-04 — Home build flow: knowledge attachments (files/links) — matches the Home page design, which lets a user attach files/links the agent should learn from before it's built — `app_v2/databases/models.py`, `app_v2/schemas/agent_build_schema.py`, `app_v2/routers/agent_build.py`, `app_v2/utils/agent_build_pipeline.py`, `migrations/versions/f3a9c1b2d4e5_*.py`, `tests/test_agent_build.py`, `app_v2/docs/home_agent_build_flow.md`

- `POST /api/v2/agent-build/` now accepts `multipart/form-data` (was JSON):
  `requirement` (unchanged validation) plus optional repeated `urls` and
  `files` fields (max 5 each, `.pdf`/`.docx`/`.txt` only for files).
- The pipeline's previously-stubbed `configuring_knowledge` stage now
  actually ingests attachments into the user's personal KB (same store as
  `app_v2/routers/personal_knowledge_base.py`) and binds them to the new
  agent once it's created. Ingestion/binding failures are logged and
  skipped per-item rather than failing the whole build.
- Added `knowledge_urls`/`knowledge_files` (JSONB) columns to
  `agent_build_jobs`.
