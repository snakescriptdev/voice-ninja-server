import sys
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

DB_URL = "postgresql://snakescriptacc:snake@localhost/voice_ninja_db"

if not DB_URL:
    print("Error: DB_URL must be set.")
    sys.exit(1)

engine = create_engine(DB_URL, pool_pre_ping=True)

# Ignore conversations shorter than this many seconds — they're too short for
# a stable per-minute rate (e.g. immediate disconnects/test calls).
MIN_DURATION_SECONDS = 1


def format_duration(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def print_conversation_credits_table(rows):
    header = f"{'id':<8}{'duration':<10}{'seconds':<9}{'credits':<10}{'credits/sec':<13}{'credits/min':<12}"
    print(header)
    print("-" * len(header))
    for row in rows:
        seconds = row["duration"]
        credits = row["actual_conversation_credits"]
        credits_per_sec = credits / seconds
        credits_per_min = credits_per_sec * 60
        print(
            f"{row['id']:<8}{format_duration(seconds):<10}{seconds:<9}{credits:<10}"
            f"{credits_per_sec:<13.2f}{credits_per_min:<12.2f}"
        )
    print("-" * len(header))


def calculate_and_update_conversation_credits_per_minute():
    """
    Cron job script to recompute the ElevenLabs conversation cost (in EL
    credits/minute) from actually billed conversations, and update
    coin_usage_settings.elevenlabs_conversation_credits_per_minute with it.

    Takes the HIGHEST per-conversation credits/minute rate among eligible
    conversations, rather than an average, so the stored rate never
    undercharges relative to the most expensive real call.

    Only updates coin_usage_settings.elevenlabs_conversation_credits_per_minute
    if the freshly computed rate is higher than the currently stored one —
    the rate is never lowered by this job.

    The select, print, and update all run inside one transaction
    (engine.begin()) so a failure anywhere here rolls back cleanly and never
    leaves coin_usage_settings partially updated — and, since this script
    only ever touches elevenlabs_conversation_credits_per_minute, a failure
    here has no way to reach usd_to_credits (see llm_cost_usd_per_min.py),
    which is computed and committed independently.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, duration, actual_conversation_credits
                FROM conversations
                WHERE call_status = 'success'
                  AND duration IS NOT NULL AND duration >= :min_duration
                  AND actual_conversation_credits IS NOT NULL AND actual_conversation_credits > 0
                """
            ),
            {"min_duration": MIN_DURATION_SECONDS},
        ).mappings().all()

        if not rows:
            print(f"[{datetime.now(timezone.utc)}] No eligible conversations found, skipping update.")
            return

        print_conversation_credits_table(rows)

        best = max(
            rows,
            key=lambda row: row["actual_conversation_credits"] / row["duration"],
        )
        best_credits_per_minute = (best["actual_conversation_credits"] / best["duration"]) * 60
        credits_per_minute = round(best_credits_per_minute)

        print(
            f"[{datetime.now(timezone.utc)}] Highest rate over {len(rows)} conversations: "
            f"conversation {best['id']} - {best['actual_conversation_credits']} credits / {best['duration']} sec "
            f"= {best_credits_per_minute:.2f} credits/min -> rounded to {credits_per_minute} credits/min"
        )

        current_value = conn.execute(
            text("SELECT elevenlabs_conversation_credits_per_minute FROM coin_usage_settings")
        ).scalar()

        if current_value is not None and credits_per_minute <= current_value:
            print(
                f"[{datetime.now(timezone.utc)}] Computed elevenlabs_conversation_credits_per_minute "
                f"({credits_per_minute}) is not greater than the current value ({current_value}), skipping update."
            )
            return

        conn.execute(
            text(
                """
                UPDATE coin_usage_settings
                SET elevenlabs_conversation_credits_per_minute = :value,
                    field_update_meta = COALESCE(field_update_meta, '{}'::jsonb) || jsonb_build_object(
                        'elevenlabs_conversation_credits_per_minute',
                        jsonb_build_object('updated_by', 'cron', 'updated_at', now())
                    ),
                    updated_by = 'cron',
                    updated_at = now()
                """
            ),
            {"value": credits_per_minute},
        )

        print(f"[{datetime.now(timezone.utc)}] Updated elevenlabs_conversation_credits_per_minute to {credits_per_minute}.")


if __name__ == "__main__":
    try:
        calculate_and_update_conversation_credits_per_minute()
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] Error updating elevenlabs_conversation_credits_per_minute: {e}")
        sys.exit(1)
