import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from app_v2.core.config import VoiceSettings

# 30 was the conversion rate in effect for the entire history before this
# feature shipped (no coin_usage_settings_versions row tracked a rate yet).
DEFAULT_CREDITS_PER_RUPEE = 30

engine = create_engine(VoiceSettings.DB_URL, pool_pre_ping=True)


def backfill_credits_per_rupee_and_conversation_cost_inr():
    """
    One-off backfill for the credits_per_rupee snapshot feature:

    1. Sets credits_per_rupee = 30 on every coin_usage_settings_versions row.
    2. Recomputes conversations.cost_inr for every existing conversation from
       the credits_per_rupee on its own settings_version (falling back to the
       same default rate for legacy conversations with no settings_version_id
       at all).

    Both steps run in one transaction, so a failure leaves neither half applied.
    """
    with engine.begin() as conn:
        versions_updated = conn.execute(
            text("UPDATE coin_usage_settings_versions SET credits_per_rupee = :rate"),
            {"rate": DEFAULT_CREDITS_PER_RUPEE},
        ).rowcount
        print(
            f"[{datetime.now(timezone.utc)}] Set credits_per_rupee = {DEFAULT_CREDITS_PER_RUPEE} "
            f"on {versions_updated} coin_usage_settings_versions row(s)."
        )

        with_version = conn.execute(
            text(
                """
                UPDATE conversations c
                SET cost_inr = ROUND(
                    COALESCE(c.coins_charged_to_user, c.cost, 0)::numeric
                    / v.credits_per_rupee::numeric,
                    2
                )
                FROM coin_usage_settings_versions v
                WHERE c.settings_version_id = v.id
                """
            )
        ).rowcount
        print(
            f"[{datetime.now(timezone.utc)}] Updated cost_inr on {with_version} "
            f"conversation(s) via their settings_version."
        )

        without_version = conn.execute(
            text(
                """
                UPDATE conversations
                SET cost_inr = ROUND(COALESCE(coins_charged_to_user, cost, 0)::numeric / CAST(:rate AS numeric), 2)
                WHERE settings_version_id IS NULL
                """
            ),
            {"rate": DEFAULT_CREDITS_PER_RUPEE},
        ).rowcount
        print(
            f"[{datetime.now(timezone.utc)}] Updated cost_inr on {without_version} legacy "
            f"conversation(s) with no settings_version (used default rate {DEFAULT_CREDITS_PER_RUPEE})."
        )


if __name__ == "__main__":
    try:
        backfill_credits_per_rupee_and_conversation_cost_inr()
    except Exception as e:
        print(f"[{datetime.now(timezone.utc)}] Error backfilling credits_per_rupee/cost_inr: {e}")
        sys.exit(1)
