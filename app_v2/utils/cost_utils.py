"""
Shared cost math for ElevenLabs voice calls — everything is in CREDITS
(1 credit == 1 user coin on our platform).

Two very different numbers live here, and they must not be conflated:

1. LIVE ESTIMATE (compute_live_charge_credits / estimate_costs_credits)
   A pre-/mid-call projection used ONLY for the low-balance cutoff. Built from:
     conversation = elevenlabs_conversation_credits_per_minute × minutes
     LLM          = agent llm_price_per_minute (USD) × minutes × usd_to_credits
     telephony    = 0 (disabled for now)
   then × (1 + markup%). The agent LLM price is a static floor (ignores tool /
   RAG runtime), so the estimate errs low on the LLM side — deliberately paired
   with markup so the cutoff still errs high. Never the bill.

2. ACTUAL COST (compute_actual_breakdown)
   Reconciled AFTER the call from ElevenLabs' real reported credits — the source
   of truth for what ElevenLabs charged us, and the basis for the profit figure.
"""
from typing import Optional, Dict, Any

from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_live_charge_credits(
    agent_llm_price_per_minute: Optional[float],
    elapsed_minutes: float,
    settings,
) -> float:
    """
    Credits we would charge the user for the call SO FAR — the number the cutoff
    compares against the user's balance.

    = (conversation credits + LLM credits + 0 telephony) × (1 + markup%)
    """
    conv_per_min = _num(getattr(settings, "elevenlabs_conversation_credits_per_minute", 0))
    usd_to_credits = _num(getattr(settings, "usd_to_credits", 0))
    markup = _num(getattr(settings, "markup_percentage", 0.0))

    conversation_credits = conv_per_min * elapsed_minutes
    llm_credits = _num(agent_llm_price_per_minute) * elapsed_minutes * usd_to_credits
    telephony_credits = 0.0

    cost_credits = conversation_credits + llm_credits + telephony_credits
    return cost_credits * (1 + markup / 100.0)


def estimate_costs_credits(
    agent_llm_price_per_minute: Optional[float],
    duration_seconds: Optional[float],
    settings,
) -> Dict[str, float]:
    """
    Our estimated COST (no markup) for a call of the given duration, broken into
    credit components — stored as the conversations.calculated_* audit columns
    so the estimate can be compared against the real ElevenLabs charge.
    """
    minutes = _num(duration_seconds) / 60.0
    conv_per_min = _num(getattr(settings, "elevenlabs_conversation_credits_per_minute", 0))
    usd_to_credits = _num(getattr(settings, "usd_to_credits", 0))

    return {
        "calculated_conversation_cost": round(conv_per_min * minutes, 4),
        "calculated_llm_cost": round(_num(agent_llm_price_per_minute) * minutes * usd_to_credits, 4),
        "calculated_telephony_cost": 0.0,
    }


def compute_actual_breakdown(
    total_elevenlabs_credits: Optional[float],
    llm_credits: Optional[float],
    coins_charged_to_user: Optional[int],
    settings=None,
) -> Dict[str, Any]:
    """
    Reconcile the real ElevenLabs charge against what we billed the user — all in
    credits, so no currency conversion is needed.

    total_elevenlabs_credits: metadata.cost (EL credits, all-in).
    llm_credits: the LLM portion split out of metadata (0/None if unavailable,
        in which case the whole cost is attributed to conversation).
    coins_charged_to_user: coins actually deducted from the user's ledger.

    profit_percentage = (charged − our_cost) / our_cost × 100 (both in credits).
        negative ⇒ loss. None when our cost is 0 (can't divide).
    """
    total_credits = _num(total_elevenlabs_credits)
    actual_llm = _num(llm_credits)
    # Never let the split go negative if the LLM figure is noisy.
    actual_conversation = max(total_credits - actual_llm, 0.0)

    charged = _num(coins_charged_to_user)
    profit_percentage = None
    if total_credits > 0:
        profit_percentage = round((charged - total_credits) / total_credits * 100.0, 2)

    return {
        "actual_llm_credits": round(actual_llm, 4),
        "actual_conversation_credits": round(actual_conversation, 4),
        "profit_percentage": profit_percentage,
    }
