"""End-to-end QA tests for the "Home build" flow:

    POST /api/v2/agent-build/         - start an async build job
    GET  /api/v2/agent-build/{job_id} - poll its status

These are real integration tests: they talk HTTP to a live server (default
http://localhost:8000 - override with AGENT_BUILD_TEST_BASE_URL) and the
pipeline itself calls the real Gemini + ElevenLabs APIs, so a full pass
proves an actual ElevenLabs agent + DB row got created, not just that the
code imports cleanly.

Auth: there's no dev-mode JWT bypass in this codebase (OTP login requires a
real inbox), so this file mints its own valid access token the same way
otp.verify_otp would - by inserting a UserSessionModel row directly and
signing a JWT with the app's own SECRET_KEY/ALGORITHM (see `_mint_token`
below). This exercises the exact same `_decode_access_token_str` /
`require_active_user` path a real login would produce.

Run with:
    .venv/bin/python -m pytest tests/test_agent_build.py -v -s
"""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get("AGENT_BUILD_TEST_BASE_URL", "http://localhost:8000")

# The pipeline makes real Gemini + ElevenLabs calls across ~7 stages, so give
# it a generous ceiling before declaring the poll loop hung.
POLL_TIMEOUT_S = 180
POLL_INTERVAL_S = 1.5

TERMINAL_STATUSES = {"completed", "failed"}
ALL_KNOWN_STATUSES = {
    "understanding_requirement",
    "generating_conversation",
    "configuring_agent",
    "configuring_knowledge",
    "configuring_voice",
    "creating_voice_agent",
    "finalizing",
    "completed",
    "failed",
}


def _db_engine():
    from sqlalchemy import create_engine

    return create_engine(os.environ["DB_URL"])


def _mint_token(user_id: int) -> str:
    """Mints a real, valid access token for `user_id` by writing a genuine
    UserSessionModel row (same table/shape the OTP login flow writes) and
    signing a JWT with the app's actual secret - so the server-side
    revocation check in jwt_utils._decode_access_token_str is exercised for
    real, not bypassed."""
    from jose import jwt as jose_jwt

    from app_v2.core.config import VoiceSettings

    jti = str(uuid.uuid4())
    engine = _db_engine()
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO user_sessions
                    (user_id, jti, device_label, user_agent, ip_address, created_at, last_used_at, is_revoked)
                VALUES
                    (:uid, :jti, 'pytest-agent-build', 'pytest', '127.0.0.1', now(), now(), false)
                """
            ),
            {"uid": user_id, "jti": jti},
        )

    payload = {
        "user_id": user_id,
        "jti": jti,
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(hours=2),
    }
    return jose_jwt.encode(payload, VoiceSettings.SECRET_KEY, algorithm=VoiceSettings.ALGORITHM)


def _first_user_id() -> int:
    from sqlalchemy import text

    engine = _db_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM unified_auth ORDER BY id LIMIT 1")).first()
    if not row:
        pytest.skip("No users in unified_auth to mint a test token for")
    return row[0]


@pytest.fixture(scope="module")
def auth_headers():
    try:
        requests.get(BASE_URL + "/", timeout=5)
    except requests.exceptions.ConnectionError:
        pytest.skip(f"Server not reachable at {BASE_URL} - start it before running this test file")

    user_id = _first_user_id()
    token = _mint_token(user_id)
    return {"Authorization": f"Bearer {token}"}, user_id


def _poll_until_terminal(job_id: int, headers: dict):
    """Polls GET /agent-build/{job_id} until a terminal status, returning
    (list_of_observed_status_dicts, final_payload)."""
    observed = []
    seen_statuses_in_order = []
    deadline = time.time() + POLL_TIMEOUT_S
    final = None

    while time.time() < deadline:
        resp = requests.get(f"{BASE_URL}/api/v2/agent-build/{job_id}", headers=headers, timeout=15)
        assert resp.status_code == 200, f"poll failed: {resp.status_code} {resp.text}"
        payload = resp.json()
        observed.append(payload)
        status = payload["status"]
        if not seen_statuses_in_order or seen_statuses_in_order[-1] != status:
            seen_statuses_in_order.append(status)
            print(f"[job {job_id}] status -> {status}")

        assert status in ALL_KNOWN_STATUSES, f"Unknown status value: {status!r}"

        if status in TERMINAL_STATUSES:
            final = payload
            break
        time.sleep(POLL_INTERVAL_S)

    assert final is not None, (
        f"Job {job_id} never reached a terminal status within {POLL_TIMEOUT_S}s "
        f"(stuck at {observed[-1]['status'] if observed else 'no observations'})"
    )
    return observed, seen_statuses_in_order, final


class TestAgentBuildHappyPath:
    def test_dental_clinic_requirement_completes_with_real_agent(self, auth_headers):
        headers, _user_id = auth_headers
        requirement = (
            "Create a voice agent for my dental clinic that answers calls, "
            "answers common questions, and books appointments"
        )

        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": requirement},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert isinstance(body["id"], int)
        assert body["status"] in ALL_KNOWN_STATUSES
        assert body["agent"] is None
        job_id = body["id"]

        observed, stage_sequence, final = _poll_until_terminal(job_id, headers)

        # Proves real progress was observed over time, not an instant jump:
        # at least 3 distinct non-terminal stages must have shown up across
        # the polling window before the terminal status.
        non_terminal_stages_seen = [s for s in stage_sequence if s not in TERMINAL_STATUSES]
        assert len(non_terminal_stages_seen) >= 3, (
            f"Expected multiple distinct intermediate stages, only saw: {stage_sequence}"
        )
        assert len(observed) >= 3, "Expected multiple poll observations, not an instant single-shot completion"

        if final["status"] == "failed":
            assert final["error_message"], "failed job must carry a readable error_message"
            pytest.fail(
                f"Build job failed (this can be a real upstream Gemini/ElevenLabs error, "
                f"not necessarily a QA bug) - error_message: {final['error_message']}"
            )

        assert final["status"] == "completed"
        assert final["error_message"] is None
        agent = final["agent"]
        assert agent is not None, "completed job must carry a populated agent object"
        assert agent["id"]
        assert agent["agent_name"]
        assert agent.get("elevenlabs_agent_id"), "completed agent must have a real ElevenLabs agent id"
        print(
            f"Created real agent: id={agent['id']} name={agent['agent_name']!r} "
            f"elevenlabs_agent_id={agent['elevenlabs_agent_id']!r}"
        )

    def test_booking_requirement_never_mentions_calendar(self, auth_headers):
        """There is no calendar integration code in this feature at all - so
        polling a booking-flavored requirement end to end must never surface
        the word 'calendar' anywhere in any response payload."""
        headers, _user_id = auth_headers
        requirement = "Create a voice agent that answers calls and books appointments"

        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": requirement},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["id"]

        observed, _stages, final = _poll_until_terminal(job_id, headers)

        for payload in observed:
            blob = str(payload).lower()
            assert "calendar" not in blob, f"Unexpected 'calendar' text in poll response: {payload}"

        if final["status"] == "failed":
            assert "calendar" not in (final["error_message"] or "").lower()


class TestAgentBuildValidation:
    def test_too_short_requirement_is_rejected_with_422_not_500(self, auth_headers):
        headers, _user_id = auth_headers
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": "hi"},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code in (400, 422), f"expected a validation error, got {resp.status_code}: {resp.text}"
        assert resp.status_code != 500
        body = resp.json()
        # FastAPI/pydantic validation errors surface under "detail" - just
        # confirm there's a readable message, not an opaque 500 stack trace.
        assert body.get("detail"), f"expected a readable validation error body, got: {body}"

    def test_empty_requirement_is_rejected(self, auth_headers):
        headers, _user_id = auth_headers
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": ""},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code in (400, 422)
        assert resp.status_code != 500

    def test_missing_requirement_field_is_rejected(self, auth_headers):
        headers, _user_id = auth_headers
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code in (400, 422)
        assert resp.status_code != 500


class TestAgentBuildAuthAndOwnership:
    def test_no_bearer_token_is_401(self):
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": "Create a voice agent for my dental clinic"},
            timeout=15,
        )
        assert resp.status_code == 401

    def test_get_nonexistent_job_is_404(self, auth_headers):
        headers, _user_id = auth_headers
        resp = requests.get(f"{BASE_URL}/api/v2/agent-build/999999999", headers=headers, timeout=15)
        assert resp.status_code == 404

    def test_get_job_owned_by_another_user_is_404_not_someone_elses_data(self, auth_headers):
        """Creates a job as user A, then confirms user B (a second minted
        token) gets 404 rather than user A's job data."""
        headers_a, user_a_id = auth_headers

        from sqlalchemy import text

        engine = _db_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM unified_auth WHERE id != :uid ORDER BY id LIMIT 1"),
                {"uid": user_a_id},
            ).first()
        if not row:
            pytest.skip("Need a second user in unified_auth to test cross-user ownership")
        user_b_id = row[0]
        token_b = _mint_token(user_b_id)
        headers_b = {"Authorization": f"Bearer {token_b}"}

        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": "Create a voice agent for my dental clinic"},
            headers=headers_a,
            timeout=15,
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["id"]

        resp_b = requests.get(f"{BASE_URL}/api/v2/agent-build/{job_id}", headers=headers_b, timeout=15)
        assert resp_b.status_code == 404, (
            f"Expected 404 for a job owned by a different user, got {resp_b.status_code}: {resp_b.text}"
        )


class TestAgentBuildKnowledgeAttachments:
    """Covers the optional "Upload files" / "Add a link" attachments (see
    app_v2/routers/agent_build.py's _clean_urls/_save_knowledge_files and the
    pipeline's _ingest_knowledge_attachments/_attach_knowledge_to_agent) —
    ingested into the new agent's personal KB, see
    app_v2/routers/personal_knowledge_base.py."""

    def test_invalid_url_scheme_is_rejected_with_400(self, auth_headers):
        headers, _user_id = auth_headers
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={
                "requirement": "Create a voice agent for my dental clinic",
                "urls": "not-a-real-url",
            },
            headers=headers,
            timeout=15,
        )
        assert resp.status_code == 400, resp.text
        assert resp.status_code != 500

    def test_too_many_urls_is_rejected_with_400(self, auth_headers):
        headers, _user_id = auth_headers
        too_many = [f"https://example.com/{i}" for i in range(10)]
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data=[("requirement", "Create a voice agent for my dental clinic")]
            + [("urls", u) for u in too_many],
            headers=headers,
            timeout=15,
        )
        assert resp.status_code == 400, resp.text

    def test_disallowed_file_extension_is_rejected_with_400(self, auth_headers):
        headers, _user_id = auth_headers
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": "Create a voice agent for my dental clinic"},
            files={"files": ("notes.exe", b"not really a program", "application/octet-stream")},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code == 400, resp.text
        assert resp.status_code != 500

    def test_empty_file_is_rejected_with_400(self, auth_headers):
        headers, _user_id = auth_headers
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": "Create a voice agent for my dental clinic"},
            files={"files": ("empty.txt", b"", "text/plain")},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code == 400, resp.text

    def test_build_with_a_valid_attached_file_still_completes(self, auth_headers):
        """A valid attachment must never block the agent build itself, even
        if the pipeline's knowledge ingestion step later fails for some other
        reason (see _ingest_knowledge_attachments's non-fatal try/except)."""
        headers, _user_id = auth_headers
        resp = requests.post(
            f"{BASE_URL}/api/v2/agent-build/",
            data={"requirement": "Create a voice agent for my dental clinic that books appointments"},
            files={"files": ("clinic_hours.txt", b"We are open 9am-5pm, Monday to Friday.", "text/plain")},
            headers=headers,
            timeout=15,
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["id"]

        _observed, _stages, final = _poll_until_terminal(job_id, headers)

        if final["status"] == "failed":
            pytest.fail(
                f"Build job failed with a valid file attachment - error_message: {final['error_message']}"
            )
        assert final["status"] == "completed"
        assert final["agent"] is not None
