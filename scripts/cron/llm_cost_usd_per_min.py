import sys
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

DB_URL = ""

if not DB_URL:
    print("Error: DB_URL must be set.")
    sys.exit(1)

engine = create_engine(DB_URL, pool_pre_ping=True)


def print_llm_cost_table(rows, usd_to_credits):
    header = (
        f"{'id':<8}{'llm_cost_usd':<14}{'expected_credits':<18}"
        f"{'actual_charged':<16}{'usd_to_credits (approx)':<24}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        cost_usd = row["total_llm_usd_price"]
        actual_credits = row["actual_llm_credits"]
        expected_credits = round(cost_usd * usd_to_credits)
        print(
            f"{row['id']:<8}${cost_usd:<13.4f}"
            f"{expected_credits:<18}{actual_credits:<16}≈ {expected_credits} credits"
        )
    print("-" * len(header))


def calculate_and_update_llm_usd_to_credits():
    """
    Cron job script to recompute the USD -> credits conversion rate (EL
    credits charged per $1 of LLM cost) from actually billed conversations,
    and update coin_usage_settings.usd_to_credits with it.

    Takes the HIGHEST per-conversation (actual_llm_credits /
    total_llm_usd_price) ratio among eligible conversations, so the stored
    rate never undercharges relative to the most expensive real call.

    Only updates coin_usage_settings.usd_to_credits if the freshly computed
    rate is higher than the currently stored one — the rate is never
    lowered by this job.

    The select, print, and update all run inside one transaction
    (engine.begin()) so a failure anywhere here rolls back cleanly and never
    leaves coin_usage_settings partially updated — and, since this script
    only ever touches usd_to_credits, a failure here has no way to reach
    elevenlabs_conversation_credits_per_minute, which is computed and
    committed independently by conv_credits_per_min_updations.py.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, total_llm_usd_price, actual_llm_credits
                FROM conversations
                WHERE call_status = 'success'
                  AND total_llm_usd_price IS NOT NULL AND total_llm_usd_price > 0
                  AND actual_llm_credits IS NOT NULL AND actual_llm_credits > 0
                """
            )
        ).mappings().all()

        if not rows:
            print(f"[{datetime.now(timezone.utc)}] No eligible conversations found, skipping update.")
            return

        best = max(rows, key=lambda row: row["actual_llm_credits"] / row["total_llm_usd_price"])
        best_ratio = best["actual_llm_credits"] / best["total_llm_usd_price"]
        usd_to_credits = round(best_ratio)

        print_llm_cost_table(rows, usd_to_credits)

        print(
            f"[{datetime.now(timezone.utc)}] Highest rate over {len(rows)} conversations: "
            f"conversation {best['id']} - {best['actual_llm_credits']} credits / ${best['total_llm_usd_price']:.4f} "
            f"= {best_ratio:.2f} credits/$ -> rounded to {usd_to_credits} credits/$"
        )

        current_value = conn.execute(
            text("SELECT usd_to_credits FROM coin_usage_settings")
        ).scalar()

        if current_value is not None and usd_to_credits <= current_value:
            print(
                f"[{datetime.now(timezone.utc)}] Computed usd_to_credits ({usd_to_credits}) is not "
                f"greater than the current value ({current_value}), skipping update."
            )
            return

        conn.execute(
            text(
                """
                UPDATE coin_usage_settings
                SET usd_to_credits = :value,
                    field_update_meta = COALESCE(field_update_meta, '{}'::jsonb) || jsonb_build_object(
                        'usd_to_credits',
                        jsonb_build_object('updated_by', 'cron', 'updated_at', now())
                    ),
                    updated_by = 'cron',
                    updated_at = now()
                """
            ),
            {"value": usd_to_credits},
        )

        print(f"[{datetime.now(timezone.utc)}] Updated usd_to_credits to {usd_to_credits}.")


if __name__ == "__main__":
    try:
        calculate_and_update_llm_usd_to_credits()
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] Error updating usd_to_credits: {e}")
        sys.exit(1)
