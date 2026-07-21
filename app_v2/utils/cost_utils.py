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

import tiktoken

from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

# A single fixed encoding used purely as a consistent RELATIVE proxy for
# prompt size (not an exact per-model billing count — agents can run on
# different LLM providers/models with their own tokenizers). What matters
# for resolve_llm_rate_basis's staleness check is that the same text tokenizes
# to the same count every time, not which exact tokenizer a given agent's
# model uses.
_TOKENIZER = tiktoken.get_encoding("cl100k_base")


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def count_tokens(text: Optional[str]) -> int:
    """Approximate token count for `text`, 0 for empty/None. See _TOKENIZER."""
    if not text:
        return 0
    return len(_TOKENIZER.encode(text))


def compute_live_charge_credits(
    agent_llm_price_per_minute: Optional[float],
    elapsed_minutes: float,
    settings,
    llm_cost_multiplier: float = 1.0,
    llm_credits_override: Optional[float] = None,
) -> float:
    """
    Credits we would charge the user for the call SO FAR — the number the cutoff
    compares against the user's balance.

    = (conversation credits + LLM credits + 0 telephony) × (1 + markup%)

    llm_cost_multiplier: admin-configured multiplier applied on top of the
    agent's bare llm_price_per_minute when the agent has a knowledge base
    and/or tools attached — both add LLM overhead (RAG retrieval, tool
    round-trips) beyond the static per-minute floor. Defaults to 1.0 (no
    adjustment) for agents with neither. Ignored when llm_credits_override
    is given.

    llm_credits_override: when provided, used directly as the LLM credits
    component instead of the agent_llm_price_per_minute-based formula. This
    is the turns-based projection learned from the agent's own last completed
    call (see resolve_llm_rate_basis / estimate_llm_credits_from_turns in
    conversation_lifecycle.py) — used for non-first calls where a fresh,
    config-matching prior call exists. Conversation and telephony credits are
    unaffected either way; only the LLM component changes.
    """
    conv_per_min = _num(getattr(settings, "elevenlabs_conversation_credits_per_minute", 0))
    usd_to_credits = _num(getattr(settings, "usd_to_credits", 0))
    markup = _num(getattr(settings, "markup_percentage", 0.0))

    conversation_credits = conv_per_min * elapsed_minutes
    if llm_credits_override is not None:
        llm_credits = _num(llm_credits_override)
    else:
        llm_credits = _num(agent_llm_price_per_minute) * elapsed_minutes * usd_to_credits * _num(llm_cost_multiplier, 1.0)
    telephony_credits = 0.0

    cost_credits = conversation_credits + llm_credits + telephony_credits
    return cost_credits * (1 + markup / 100.0)


def estimate_costs_credits(
    agent_llm_price_per_minute: Optional[float],
    duration_seconds: Optional[float],
    settings,
    llm_cost_multiplier: float = 1.0,
) -> Dict[str, float]:
    """
    Our estimated COST (no markup) for a call of the given duration, broken into
    credit components — stored as the conversations.calculated_* audit columns
    so the estimate can be compared against the real ElevenLabs charge.

    llm_cost_multiplier: see compute_live_charge_credits.
    """
    minutes = _num(duration_seconds) / 60.0
    conv_per_min = _num(getattr(settings, "elevenlabs_conversation_credits_per_minute", 0))
    usd_to_credits = _num(getattr(settings, "usd_to_credits", 0))

    return {
        "calculated_conversation_cost": round(conv_per_min * minutes, 4),
        "calculated_llm_cost": round(_num(agent_llm_price_per_minute) * minutes * usd_to_credits * _num(llm_cost_multiplier, 1.0), 4),
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
