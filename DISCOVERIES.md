## 2026-09-04
- `app_v2/routers/personal_knowledge_base.py`'s "private" (`_`-prefixed)
  helpers — `_store_kb_entry`, and the module's `ALLOWED_EXTENSIONS`/
  `MAX_FILE_SIZE_IN_MB`/`UPLOAD_DIR` constants — are already reused
  cross-router (`app_v2/routers/public_api.py` imports `_store_kb_entry as
  _store_personal_kb_entry`; `app_v2/utils/agent_build_pipeline.py` now
  does the same). This is the established pattern for ingesting a
  file/URL into the personal KB from anywhere else in the codebase — don't
  reimplement chunk/embed/store logic, import from here.
- `agent_build_pipeline.py`'s `run_agent_build_job` already had a
  `configuring_knowledge` status stage before any file/link ingestion
  existed — it was a placeholder status only. If you see a pipeline status
  enum value with no corresponding logic, check whether it's genuinely
  unimplemented before assuming it's dead.
- `tests/test_agent_build.py` is a real integration test suite (hits a
  live server + real Gemini/ElevenLabs APIs), not mocked — an intentional,
  documented exception to the "mock external services" testing rule in the
  root CLAUDE.md, because the Home build pipeline's whole value proposition
  is that the async job walks through real stages against real providers.
  It requires `AGENT_BUILD_TEST_BASE_URL`/`DB_URL` env and a running
  server; it `pytest.skip`s cleanly if the server isn't reachable.
