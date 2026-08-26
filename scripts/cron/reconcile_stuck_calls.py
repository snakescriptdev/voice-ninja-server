import sys
from datetime import datetime, timezone

import requests

# Base URL of this app's own API (e.g. "https://api.voiceninja.ai" or
# "http://127.0.0.1:8000" if the cron job runs on the same host as the app).
BASE_URL = "https://server.voiceninja.ai"

# Must match app_v2/core/config.py's INTERNAL_API_SECRET_KEY (env var
# INTERNAL_API_SECRET_KEY on the server running the app) — sent as
# `Authorization: Bearer <this>`.
INTERNAL_API_SECRET_KEY = "7e02fd66f79be438bfaa1dfba6fc1b50bb19b4338a1c5f329cbcfdb4c4fe46c0"

# How many stuck rows to reconcile in a single run — matches the endpoint's
# own default, set here explicitly so it's visible/tunable from the cron job.
# Kept small on purpose: the cron re-runs periodically anyway, so there's no
# benefit to a large batch, only a bigger risk of this request timing out.
MAX_ROWS = 10

REQUEST_TIMEOUT_SECONDS = 120

if not BASE_URL or not INTERNAL_API_SECRET_KEY:
    print("Error: BASE_URL and INTERNAL_API_SECRET_KEY must be set.")
    sys.exit(1)


def reconcile_stuck_calls():
    """
    Cron job: calls this app's own internal /reconcile-stuck-calls endpoint,
    which finds conversation rows stuck in `in_progress` (e.g. the process
    handling the call crashed or the server restarted mid-call), checks each
    one's real status on ElevenLabs, and finalizes any that actually ended —
    coins deducted, cost-audit fields filled in, and the same admin/user
    alert emails a normal call-end would trigger. All of that logic lives
    server-side in app_v2/routers/internal_reconciliation.py; this script is
    just the trigger.
    """
    url = f"{BASE_URL.rstrip('/')}/api/v2/internal/reconcile-stuck-calls"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {INTERNAL_API_SECRET_KEY}"},
        params={"max_rows": MAX_ROWS},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    summary = response.json()

    print(
        f"[{datetime.now(timezone.utc)}] Reconciliation run: "
        f"checked={summary.get('checked')} finalized={summary.get('finalized')} "
        f"still_in_progress={summary.get('still_in_progress')} "
        f"still_processing={summary.get('still_processing')} "
        f"errors={len(summary.get('errors') or [])}"
    )
    for error in summary.get("errors") or []:
        print(f"  - {error}")


if __name__ == "__main__":
    try:
        reconcile_stuck_calls()
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] Error running call reconciliation: {e}")
        sys.exit(1)
