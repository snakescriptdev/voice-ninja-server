"""
downgrade_utils.py
────────────────────────────────────────────────────────────────────────────────
Handles automatic resource enforcement when a user downgrades their subscription
plan.

Policy rules (per resource type):
  ai_voice_agents     → Order by fewest ConversationsModel count, then oldest
                        created_at. Set is_enabled = False on excess.
  web_voice_agent     → Order by fewest WebAgentLeadModel count, then oldest
                        created_at. Set is_enabled = False on excess.
  phone_numbers       → Unassigned first, then oldest created_at. Set
                        status = unassigned, clear assigned_to on excess.
  custom_voice_cloning→ Voices with no agents attached first, then oldest
                        created_at. Detach from agents by re-pointing
                        agent.agent_voice to the system default voice.
  knowledge_base      → Usage-based (MB). No disabling — creation guard
                        re-enforces at next attempt.
  monthly_minutes     → Usage-based. No disabling — read-only history.

Design decisions:
  • The session is ALWAYS passed in from the caller (verify() or webhook
    handler) so all mutations happen inside the same DB transaction.
    No commits are issued here.
  • compute_downgrade_diff() is pure read — no side effects, safe to call
    from the preview endpoint.
  • enforce_downgrade_for_user() returns a structured summary dict that is
    logged via ActivityLogModel and returned to the frontend.
  • ElevenLabs sync is intentionally NOT attempted here — the agent/voice
    objects are already marked disabled in DB. A background task or the next
    user-triggered edit should sync the disabled state to ElevenLabs to avoid
    blocking the plan-change transaction on an external API call.
"""

from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app_v2.databases.models import (
    AgentModel,
    WebAgentModel,
    WebAgentLeadModel,
    PhoneNumberService,
    VoiceModel,
    ConversationsModel,
    PlanFeatureModel,
    PlanModel,
    KnowledgeBaseModel,
    AgentKnowledgeBaseBridge,
)
from app_v2.schemas.enum_types import PhoneNumberAssignStatus
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Feature keys that have count-based or per-item limits
# ──────────────────────────────────────────────────────────────────────────────

COUNT_BASED_FEATURES = {
    "ai_voice_agents",
    "web_voice_agent",
    "phone_numbers",
    "custom_voice_cloning",
    "knowledge_base",
}

# ──────────────────────────────────────────────────────────────────────────────
# Downgrade diff computation  (pure read — no side effects)
# ──────────────────────────────────────────────────────────────────────────────

def compute_downgrade_diff(
    old_plan_id: int,
    new_plan_id: int,
    session: Session,
) -> Dict[str, Dict[str, Any]]:
    """
    Compare feature limits between old and new plan.

    Returns only the features that are being REDUCED (new_limit < old_limit),
    limited to count-based features that can actually be enforced.

    Return shape:
    {
      "ai_voice_agents": {"old_limit": 5, "new_limit": 2},
      "phone_numbers":   {"old_limit": 3, "new_limit": 1},
    }

    None limit means unlimited in the plan_features table.
    If a feature is unlimited in the old plan but limited in the new one,
    it is included. If both are unlimited, it is excluded.
    """
    old_features: List[PlanFeatureModel] = (
        session.query(PlanFeatureModel)
        .filter(PlanFeatureModel.plan_id == old_plan_id)
        .all()
    )
    new_features: List[PlanFeatureModel] = (
        session.query(PlanFeatureModel)
        .filter(PlanFeatureModel.plan_id == new_plan_id)
        .all()
    )

    old_map: Dict[str, Optional[int]] = {
        f.feature_key: f.limit for f in old_features
    }
    new_map: Dict[str, Optional[int]] = {
        f.feature_key: f.limit for f in new_features
    }

    # Only features the OLD plan actually has can be downgraded.
    # If a feature is not in the old plan, the user never had it — skip.
    # Iterate over old plan's count-based features only.
    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    diff: Dict[str, Dict[str, Any]] = {}

    for feature_key in COUNT_BASED_FEATURES:
        # Rule 1: If NOT in the current (old) plan — skip. Can't downgrade what you don't have.
        if feature_key not in old_keys:
            continue

        old_limit = old_map[feature_key]  # None = unlimited in old plan

        # Rule 2: If feature is present in old plan but ABSENT from new plan —
        # that is definitely a downgrade (feature being removed entirely).
        if feature_key not in new_keys:
            diff[feature_key] = {
                "old_limit": old_limit,
                "new_limit": 0,          # 0 = feature removed from new plan
                "feature_removed": True,
            }
            continue

        new_limit = new_map[feature_key]  # None = unlimited in new plan

        # Rule 3: New plan is also unlimited — no downgrade.
        if new_limit is None:
            continue

        # Rule 4: Old was unlimited but new is now limited, OR new limit < old limit.
        if old_limit is None or new_limit < old_limit:
            diff[feature_key] = {
                "old_limit": old_limit,
                "new_limit": new_limit,
                "feature_removed": False,
            }

    return diff


def compute_downgrade_preview(
    user_id: int,
    old_plan_id: int,
    new_plan_id: int,
    session: Session,
) -> Dict[str, Any]:
    """
    Extended version of compute_downgrade_diff that also calculates current
    usage and how many resources will be affected.

    Used by the GET /downgrade-preview endpoint.

    Return shape:
    {
      "is_downgrade": True,
      "affected_features": {
        "ai_voice_agents": {
          "old_limit": 5,
          "new_limit": 2,
          "current_usage": 4,
          "will_disable_count": 2,
          "affected_resource_names": ["Agent A", "Agent B"],
          "message": "2 of your 4 agents will be auto-disabled (fewest calls first)"
        },
        ...
      }
    }
    """
    diff = compute_downgrade_diff(old_plan_id, new_plan_id, session)

    if not diff:
        return {"is_downgrade": False, "affected_features": {}}

    affected: Dict[str, Any] = {}

    for feature_key, limits in diff.items():
        new_limit: int = limits["new_limit"]
        feature_removed: bool = limits.get("feature_removed", False)

        if feature_removed:
            # Feature gone entirely — all current resources are affected
            current_usage = _get_current_count(user_id, feature_key, session)
            will_disable = current_usage
            resource_names = _get_affected_resource_names(user_id, feature_key, 0, session) if will_disable > 0 else []
        elif feature_key == "knowledge_base":
            # For KB, usage is total files, but will_disable is files > limit
            total_files = (
                session.query(func.count(KnowledgeBaseModel.id))
                .filter(KnowledgeBaseModel.user_id == user_id)
                .scalar() or 0
            )
            over_limit_count = (
                session.query(func.count(KnowledgeBaseModel.id))
                .filter(
                    KnowledgeBaseModel.user_id == user_id,
                    KnowledgeBaseModel.file_size > new_limit * 1024  # MB to KB
                )
                .scalar() or 0
            )
            current_usage = total_files
            will_disable = over_limit_count
            resource_names = _get_affected_resource_names(user_id, feature_key, new_limit, session) if will_disable > 0 else []
        else:
            current_usage = _get_current_count(user_id, feature_key, session)
            will_disable = max(0, current_usage - new_limit)
            resource_names = _get_affected_resource_names(user_id, feature_key, new_limit, session) if will_disable > 0 else []

        message = _build_preview_message(feature_key, current_usage, new_limit, will_disable, feature_removed)

        affected[feature_key] = {
            "old_limit": limits["old_limit"],
            "new_limit": new_limit if not feature_removed else None,
            "feature_removed": feature_removed,
            "current_usage": current_usage,
            "will_disable_count": will_disable,
            "affected_resource_names": resource_names,
            "message": message,
        }

    return {
        "is_downgrade": True,
        "affected_features": affected,
    }


def _get_affected_resource_names(user_id: int, feature_key: str, new_limit: int, session: Session) -> List[str]:
    """
    Identifies which specific resources will be disabled/affected based on enforcement logic.
    Returns names (or identifiers) of those resources.
    """
    if feature_key == "ai_voice_agents":
        conv_count_sub = (
            session.query(
                ConversationsModel.agent_id,
                func.count(ConversationsModel.id).label("conv_count"),
            )
            .group_by(ConversationsModel.agent_id)
            .subquery()
        )
        enabled_agents = (
            session.query(AgentModel)
            .outerjoin(conv_count_sub, AgentModel.id == conv_count_sub.c.agent_id)
            .filter(AgentModel.user_id == user_id, AgentModel.is_enabled == True)
            .order_by(
                func.coalesce(conv_count_sub.c.conv_count, 0).asc(),
                AgentModel.created_at.asc(),
            )
            .all()
        )
        total = len(enabled_agents)
        if total > new_limit:
            to_disable = enabled_agents[: total - new_limit]
            return [a.name for a in to_disable]

    elif feature_key == "web_voice_agent":
        lead_count_sub = (
            session.query(
                WebAgentLeadModel.web_agent_id,
                func.count(WebAgentLeadModel.id).label("lead_count"),
            )
            .group_by(WebAgentLeadModel.web_agent_id)
            .subquery()
        )
        enabled_web_agents = (
            session.query(WebAgentModel)
            .outerjoin(lead_count_sub, WebAgentModel.id == lead_count_sub.c.web_agent_id)
            .filter(WebAgentModel.user_id == user_id, WebAgentModel.is_enabled == True)
            .order_by(
                func.coalesce(lead_count_sub.c.lead_count, 0).asc(),
                WebAgentModel.created_at.asc(),
            )
            .all()
        )
        total = len(enabled_web_agents)
        if total > new_limit:
            to_disable = enabled_web_agents[: total - new_limit]
            return [wa.name for wa in to_disable]

    elif feature_key == "phone_numbers":
        all_phones = (
            session.query(PhoneNumberService)
            .filter(PhoneNumberService.user_id == user_id)
            .order_by(
                (PhoneNumberService.status == PhoneNumberAssignStatus.assigned).asc(),
                PhoneNumberService.created_at.asc(),
            )
            .all()
        )
        total = len(all_phones)
        if total > new_limit:
            to_unassign = all_phones[: total - new_limit]
            return [p.phone_number for p in to_unassign]

    elif feature_key == "custom_voice_cloning":
        agent_count_sub = (
            session.query(
                AgentModel.agent_voice,
                func.count(AgentModel.id).label("agent_count"),
            )
            .group_by(AgentModel.agent_voice)
            .subquery()
        )
        custom_voices = (
            session.query(VoiceModel)
            .outerjoin(agent_count_sub, VoiceModel.id == agent_count_sub.c.agent_voice)
            .filter(VoiceModel.user_id == user_id, VoiceModel.is_custom_voice == True)
            .order_by(
                func.coalesce(agent_count_sub.c.agent_count, 0).asc(),
                VoiceModel.created_at.asc(),
            )
            .all()
        )
        total = len(custom_voices)
        if total > new_limit:
            to_detach = custom_voices[: total - new_limit]
            return [v.name or f"Voice {v.id}" for v in to_detach]

    elif feature_key == "knowledge_base":
        if new_limit == 0:
            # Feature removed entirely — all KB files are affected
            all_kbs = (
                session.query(KnowledgeBaseModel)
                .filter(KnowledgeBaseModel.user_id == user_id)
                .all()
            )
            return [kb.title or f"File {kb.id}" for kb in all_kbs]
        over_limit_kbs = (
            session.query(KnowledgeBaseModel)
            .filter(
                KnowledgeBaseModel.user_id == user_id,
                KnowledgeBaseModel.file_size > new_limit * 1024
            )
            .all()
        )
        return [kb.title or f"File {kb.id}" for kb in over_limit_kbs]

    return []


def _get_current_count(user_id: int, feature_key: str, session: Session) -> int:
    """Count currently active/owned resources for a user by feature key."""
    if feature_key == "ai_voice_agents":
        return (
            session.query(func.count(AgentModel.id))
            .filter(
                AgentModel.user_id == user_id,
                AgentModel.is_enabled == True,
            )
            .scalar() or 0
        )
    elif feature_key == "web_voice_agent":
        return (
            session.query(func.count(WebAgentModel.id))
            .filter(
                WebAgentModel.user_id == user_id,
                WebAgentModel.is_enabled == True,
            )
            .scalar() or 0
        )
    elif feature_key == "phone_numbers":
        return (
            session.query(func.count(PhoneNumberService.id))
            .filter(PhoneNumberService.user_id == user_id)
            .scalar() or 0
        )
    elif feature_key == "custom_voice_cloning":
        return (
            session.query(func.count(VoiceModel.id))
            .filter(
                VoiceModel.user_id == user_id,
                VoiceModel.is_custom_voice == True,
            )
            .scalar() or 0
        )
    elif feature_key == "knowledge_base":
        return (
            session.query(func.count(KnowledgeBaseModel.id))
            .filter(KnowledgeBaseModel.user_id == user_id)
            .scalar() or 0
        )
    return 0


def _build_preview_message(
    feature_key: str,
    current_usage: int,
    new_limit: int,
    will_disable: int,
    feature_removed: bool = False,
) -> str:
    """
    Builds a human-readable description of what will happen
    for each feature that is being restricted in the new plan.
    """
    # ── Feature completely removed from new plan ────────────────────────────────
    if feature_removed:
        if current_usage == 0:
            removed_no_usage = {
                "ai_voice_agents": "The new plan does not include AI voice agents. You have none currently, so no immediate impact.",
                "web_voice_agent": "The new plan does not include web agents. You have none currently, so no immediate impact.",
                "phone_numbers": "The new plan does not include phone numbers. You have none currently, so no immediate impact.",
                "custom_voice_cloning": "The new plan does not include custom voice cloning. You have none currently, so no immediate impact.",
                "knowledge_base": "The new plan does not include knowledge bases. You have none currently, so no immediate impact.",
            }
            return removed_no_usage.get(
                feature_key,
                "This feature is not included in the new plan but you have no resources using it.",
            )
        removed_with_usage = {
            "ai_voice_agents": (
                f"The new plan does not include AI voice agents. "
                f"All {current_usage} of your active agent(s) will be automatically disabled."
            ),
            "web_voice_agent": (
                f"The new plan does not include web agents. "
                f"All {current_usage} of your active web agent(s) will be automatically disabled."
            ),
            "phone_numbers": (
                f"The new plan does not include phone numbers. "
                f"All {current_usage} of your phone number(s) will be unassigned from agents."
            ),
            "custom_voice_cloning": (
                f"The new plan does not include custom voice cloning. "
                f"All {current_usage} of your custom voice(s) will be detached from agents. "
                f"Voices are NOT deleted — you can re-attach them if you upgrade."
            ),
            "knowledge_base": (
                f"The new plan does not include knowledge bases. "
                f"All {current_usage} of your knowledge base file(s) will be unbound from agents."
            ),
        }
        return removed_with_usage.get(
            feature_key,
            f"This feature is not included in the new plan. All {current_usage} resource(s) will be affected.",
        )

    # ── Feature present but limit reduced ─────────────────────────────────────
    if will_disable == 0:
        # User is within the new limit already — no action needed
        safe_messages = {
            "ai_voice_agents": (
                f"Your new plan allows up to {new_limit} active AI voice agent(s). "
                f"You currently have {current_usage} — no agents will be disabled."
            ),
            "web_voice_agent": (
                f"Your new plan allows up to {new_limit} active web agent(s). "
                f"You currently have {current_usage} — no web agents will be disabled."
            ),
            "phone_numbers": (
                f"Your new plan allows up to {new_limit} phone number(s). "
                f"You currently have {current_usage} — no numbers will be unassigned."
            ),
            "custom_voice_cloning": (
                f"Your new plan allows up to {new_limit} custom voice(s). "
                f"You currently have {current_usage} — no voices will be detached."
            ),
            "knowledge_base": (
                f"Your new plan sets a per-file limit of {new_limit}MB. "
                f"You currently have {current_usage} file(s) — none exceed the new limit."
            ),
        }
        return safe_messages.get(
            feature_key,
            f"You are within the new plan's limits for this feature — no action needed.",
        )

    # User exceeds the new limit — resources will be auto-adjusted
    action_messages = {
        "ai_voice_agents": (
            f"Your new plan only allows {new_limit} active AI voice agent(s), but you currently "
            f"have {current_usage}. {will_disable} agent(s) will be automatically disabled "
            f"(those with the fewest calls are disabled first). "
            f"You will keep your {new_limit} most-used agent(s) active."
        ),
        "web_voice_agent": (
            f"Your new plan only allows {new_limit} active web agent(s), but you currently "
            f"have {current_usage}. {will_disable} web agent(s) will be automatically disabled "
            f"(those with the fewest leads are disabled first). "
            f"You will keep your {new_limit} most-used web agent(s) active."
        ),
        "phone_numbers": (
            f"Your new plan only allows {new_limit} phone number(s), but you currently "
            f"have {current_usage}. {will_disable} phone number(s) will be automatically "
            f"unassigned from agents (unassigned numbers are removed first, then oldest). "
            f"You will keep {new_limit} assigned number(s)."
        ),
        "custom_voice_cloning": (
            f"Your new plan only allows {new_limit} custom voice(s), but you currently "
            f"have {current_usage}. {will_disable} custom voice(s) will be automatically "
            f"detached from any agents using them (voices attached to no agents are removed "
            f"first). Detached voices are NOT deleted — you can re-attach them if you upgrade. "
            f"Affected agents will fall back to the system default voice."
        ),
        "knowledge_base": (
            f"Your new plan enforces a {new_limit}MB per-file limit. {will_disable} of your "
            f"{current_usage} knowledge base file(s) exceed this limit and will be automatically "
            f"unbound from all agents. The files themselves are not deleted — you can manage "
            f"them after the plan change."
        ),
    }
    return action_messages.get(
        feature_key,
        f"{will_disable} of your {current_usage} resource(s) exceed the new plan's "
        f"limit of {new_limit} and will be automatically adjusted.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main enforcement entry point
# ──────────────────────────────────────────────────────────────────────────────

def enforce_downgrade_for_user(
    user_id: int,
    old_plan_id: int,
    new_plan_id: int,
    session: Session,
) -> Dict[str, Any]:
    """
    Computes the downgrade diff and enforces resource limits for each
    affected feature key.

    IMPORTANT: Does NOT commit. The caller owns the transaction.

    Returns a summary dict describing what was changed:
    {
      "ai_voice_agents": {"disabled_ids": [3, 7], "kept_enabled_ids": [1, 2]},
      "web_voice_agent":  {"disabled_ids": [5]},
      "phone_numbers":    {"unassigned_ids": [8], "kept_ids": [2]},
      "custom_voice_cloning": {"detached_voice_ids": [4], "kept_ids": [1, 2]},
    }
    """
    diff = compute_downgrade_diff(old_plan_id, new_plan_id, session)

    if not diff:
        logger.info(
            f"enforce_downgrade_for_user | user={user_id} | "
            f"no count-based features reduced — nothing to enforce"
        )
        return {}

    summary: Dict[str, Any] = {}

    for feature_key, limits in diff.items():
        new_limit: int = limits["new_limit"]

        logger.info(
            f"enforce_downgrade_for_user | user={user_id} | "
            f"feature={feature_key} | "
            f"old_limit={limits['old_limit']} → new_limit={new_limit}"
        )

        if feature_key == "ai_voice_agents":
            result = _enforce_ai_voice_agents(user_id, new_limit, session)
            summary["ai_voice_agents"] = result

        elif feature_key == "web_voice_agent":
            result = _enforce_web_voice_agents(user_id, new_limit, session)
            summary["web_voice_agent"] = result

        elif feature_key == "phone_numbers":
            result = _enforce_phone_numbers(user_id, new_limit, session)
            summary["phone_numbers"] = result

        elif feature_key == "custom_voice_cloning":
            result = _enforce_custom_voices(user_id, new_limit, session)
            summary["custom_voice_cloning"] = result

        elif feature_key == "knowledge_base":
            result = _enforce_kb_size_limits(user_id, new_limit, session)
            summary["knowledge_base"] = result

    logger.info(
        f"enforce_downgrade_for_user | user={user_id} | summary={summary}"
    )
    return summary


# ──────────────────────────────────────────────────────────────────────────────
# Per-resource enforcers
# ──────────────────────────────────────────────────────────────────────────────

def _enforce_ai_voice_agents(
    user_id: int,
    new_limit: int,
    session: Session,
) -> Dict[str, Any]:
    """
    Disable excess AI voice agents.

    Ordering (least valuable first):
      1. Fewest total conversations (ConversationsModel count) ASC
      2. Oldest created_at ASC (tiebreaker)

    Only currently enabled agents are considered — already-disabled agents
    are ignored since they don't count against the enabled limit.

    Sets is_enabled = False on excess agents.
    Also unassigns phone numbers from disabled agents (see phone cascade below).
    """
    # Subquery: conversation count per agent
    conv_count_sub = (
        session.query(
            ConversationsModel.agent_id,
            func.count(ConversationsModel.id).label("conv_count"),
        )
        .group_by(ConversationsModel.agent_id)
        .subquery()
    )

    # All currently enabled agents for this user, ordered by policy
    enabled_agents = (
        session.query(AgentModel)
        .outerjoin(conv_count_sub, AgentModel.id == conv_count_sub.c.agent_id)
        .filter(
            AgentModel.user_id == user_id,
            AgentModel.is_enabled == True,
        )
        .order_by(
            func.coalesce(conv_count_sub.c.conv_count, 0).asc(),
            AgentModel.created_at.asc(),
        )
        .all()
    )

    total_enabled = len(enabled_agents)

    if total_enabled <= new_limit:
        # Already within limits — nothing to do
        return {
            "disabled_ids": [],
            "kept_enabled_ids": [a.id for a in enabled_agents],
        }

    # Agents to disable = everything beyond new_limit (tail of ordered list)
    # Keep the first new_limit (most active / newest)
    agents_to_keep = enabled_agents[total_enabled - new_limit:]
    agents_to_disable = enabled_agents[:total_enabled - new_limit]

    disabled_ids: List[int] = []
    for agent in agents_to_disable:
        agent.is_enabled = False
        disabled_ids.append(agent.id)

        # Cascade: unassign phone numbers attached to this agent
        for phone in agent.phone_number:
            phone.assigned_to = None
            phone.status = PhoneNumberAssignStatus.unassigned
            logger.info(
                f"_enforce_ai_voice_agents | cascade unassign phone={phone.id} "
                f"from disabled agent={agent.id}"
            )

    logger.info(
        f"_enforce_ai_voice_agents | user={user_id} | "
        f"disabled={disabled_ids} | kept={[a.id for a in agents_to_keep]}"
    )

    return {
        "disabled_ids": disabled_ids,
        "kept_enabled_ids": [a.id for a in agents_to_keep],
    }


def _enforce_web_voice_agents(
    user_id: int,
    new_limit: int,
    session: Session,
) -> Dict[str, Any]:
    """
    Disable excess web voice agents.

    Ordering (least valuable first):
      1. Fewest total leads (WebAgentLeadModel count) ASC
      2. Oldest created_at ASC (tiebreaker)

    Only currently enabled web agents are considered.
    Sets is_enabled = False on excess.
    """
    # Subquery: lead count per web agent
    lead_count_sub = (
        session.query(
            WebAgentLeadModel.web_agent_id,
            func.count(WebAgentLeadModel.id).label("lead_count"),
        )
        .group_by(WebAgentLeadModel.web_agent_id)
        .subquery()
    )

    enabled_web_agents = (
        session.query(WebAgentModel)
        .outerjoin(lead_count_sub, WebAgentModel.id == lead_count_sub.c.web_agent_id)
        .filter(
            WebAgentModel.user_id == user_id,
            WebAgentModel.is_enabled == True,
        )
        .order_by(
            func.coalesce(lead_count_sub.c.lead_count, 0).asc(),
            WebAgentModel.created_at.asc(),
        )
        .all()
    )

    total_enabled = len(enabled_web_agents)

    if total_enabled <= new_limit:
        return {
            "disabled_ids": [],
            "kept_enabled_ids": [wa.id for wa in enabled_web_agents],
        }

    web_agents_to_keep = enabled_web_agents[total_enabled - new_limit:]
    web_agents_to_disable = enabled_web_agents[:total_enabled - new_limit]

    disabled_ids: List[int] = []
    for web_agent in web_agents_to_disable:
        web_agent.is_enabled = False
        disabled_ids.append(web_agent.id)

    logger.info(
        f"_enforce_web_voice_agents | user={user_id} | "
        f"disabled={disabled_ids} | kept={[wa.id for wa in web_agents_to_keep]}"
    )

    return {
        "disabled_ids": disabled_ids,
        "kept_enabled_ids": [wa.id for wa in web_agents_to_keep],
    }


def _enforce_phone_numbers(
    user_id: int,
    new_limit: int,
    session: Session,
) -> Dict[str, Any]:
    """
    Unassign excess phone numbers.

    Ordering (least valuable first):
      1. Already unassigned (status = unassigned) first — these are low-impact
      2. Oldest created_at ASC (tiebreaker)

    Sets status = unassigned and clears assigned_to on excess numbers.
    Does NOT release/delete the Twilio number — that is a separate admin
    action with billing implications.
    """
    all_phones = (
        session.query(PhoneNumberService)
        .filter(PhoneNumberService.user_id == user_id)
        .order_by(
            # unassigned = 0 sorts before assigned = 1
            (PhoneNumberService.status == PhoneNumberAssignStatus.assigned).asc(),
            PhoneNumberService.created_at.asc(),
        )
        .all()
    )

    total = len(all_phones)

    if total <= new_limit:
        return {
            "unassigned_ids": [],
            "kept_ids": [p.id for p in all_phones],
        }

    phones_to_keep = all_phones[total - new_limit:]
    phones_to_unassign = all_phones[:total - new_limit]

    unassigned_ids: List[int] = []
    for phone in phones_to_unassign:
        phone.assigned_to = None
        phone.status = PhoneNumberAssignStatus.unassigned
        unassigned_ids.append(phone.id)

    logger.info(
        f"_enforce_phone_numbers | user={user_id} | "
        f"unassigned={unassigned_ids} | kept={[p.id for p in phones_to_keep]}"
    )

    return {
        "unassigned_ids": unassigned_ids,
        "kept_ids": [p.id for p in phones_to_keep],
    }


def _enforce_custom_voices(
    user_id: int,
    new_limit: int,
    session: Session,
) -> Dict[str, Any]:
    """
    Detach excess custom voices from agents by re-pointing agent.agent_voice
    to the system default voice.

    Ordering (least valuable first):
      1. Voices with NO agents attached (agent count = 0) first
      2. Oldest created_at ASC (tiebreaker)

    The voice record itself is NOT deleted. It remains in custom_voices so the
    user can re-attach or delete it manually. Agents that were using the
    detached voice are re-pointed to the system default (user_id IS NULL,
    is_custom_voice = False).

    System default voice: first VoiceModel where user_id IS NULL and
    is_custom_voice = False, ordered by id ASC. Falls back to the first
    non-custom voice available if no global default exists.
    """
    system_default_voice = _get_system_default_voice(session)

    if system_default_voice is None:
        # Safety: if no system voice exists, skip enforcement and warn
        logger.warning(
            f"_enforce_custom_voices | user={user_id} | "
            f"no system default voice found — skipping voice enforcement"
        )
        return {
            "detached_voice_ids": [],
            "kept_ids": [],
            "warning": "No system default voice found. Voice enforcement skipped.",
        }

    # Subquery: count of agents per voice
    agent_count_sub = (
        session.query(
            AgentModel.agent_voice,
            func.count(AgentModel.id).label("agent_count"),
        )
        .group_by(AgentModel.agent_voice)
        .subquery()
    )

    custom_voices = (
        session.query(VoiceModel)
        .outerjoin(agent_count_sub, VoiceModel.id == agent_count_sub.c.agent_voice)
        .filter(
            VoiceModel.user_id == user_id,
            VoiceModel.is_custom_voice == True,
        )
        .order_by(
            # 0 agents attached sorts first (nulls from outerjoin treated as 0)
            func.coalesce(agent_count_sub.c.agent_count, 0).asc(),
            VoiceModel.created_at.asc(),
        )
        .all()
    )

    total = len(custom_voices)

    if total <= new_limit:
        return {
            "detached_voice_ids": [],
            "kept_ids": [v.id for v in custom_voices],
        }

    voices_to_keep = custom_voices[total - new_limit:]
    voices_to_detach = custom_voices[:total - new_limit]
    voices_to_detach_ids = {v.id for v in voices_to_detach}

    detached_voice_ids: List[int] = []
    reassigned_agent_ids: List[int] = []

    for voice in voices_to_detach:
        detached_voice_ids.append(voice.id)
    #disable detached voices
    for voice in voices_to_detach:
        voice.is_enabled = False
        session.add(voice)
        session.commit()

        # Find all agents using this voice and re-point to system default
        affected_agents = (
            session.query(AgentModel)
            .filter(
                AgentModel.user_id == user_id,
                AgentModel.agent_voice == voice.id,
            )
            .all()
        )

        for agent in affected_agents:
            agent.agent_voice = system_default_voice.id
            reassigned_agent_ids.append(agent.id)
            logger.info(
                f"_enforce_custom_voices | re-pointed agent={agent.id} "
                f"from custom_voice={voice.id} to system_voice={system_default_voice.id}"
            )

    logger.info(
        f"_enforce_custom_voices | user={user_id} | "
        f"detached_voices={detached_voice_ids} | "
        f"reassigned_agents={reassigned_agent_ids} | "
        f"kept_voices={[v.id for v in voices_to_keep]}"
    )

    return {
        "detached_voice_ids": detached_voice_ids,
        "reassigned_agent_ids": reassigned_agent_ids,
        "kept_ids": [v.id for v in voices_to_keep],
        "system_default_voice_id": system_default_voice.id,
    }


def _get_system_default_voice(session: Session) -> Optional[VoiceModel]:
    """
    Fetch the system-wide default voice (not owned by any user,
    not a custom voice). Used as the fallback when detaching custom voices.

    Preference order:
      1. user_id IS NULL AND is_custom_voice = False  (global system voice)
      2. is_custom_voice = False  (any non-custom voice as last resort)
    """
    default = (
        session.query(VoiceModel)
        .filter(
            VoiceModel.user_id == None,
            VoiceModel.is_custom_voice == False,
        )
        .order_by(VoiceModel.id.asc())
        .first()
    )

    if default:
        return default

    # Fallback: any non-custom voice
    fallback = (
        session.query(VoiceModel)
        .filter(VoiceModel.is_custom_voice == False)
        .order_by(VoiceModel.id.asc())
        .first()
    )

    return fallback


def _enforce_kb_size_limits(
    user_id: int,
    new_limit_mb: int,
    session: Session,
) -> Dict[str, Any]:
    """
    Unbind KB files that exceed the new MB limit.
    DB stores file_size in KB.
    """
    limit_kb = new_limit_mb * 1024

    over_limit_kbs = (
        session.query(KnowledgeBaseModel)
        .filter(
            KnowledgeBaseModel.user_id == user_id,
            KnowledgeBaseModel.file_size > limit_kb
        )
        .all()
    )

    if not over_limit_kbs:
        return {"unbound_kb_ids": [], "agent_ids_to_sync": []}

    kb_ids = [kb.id for kb in over_limit_kbs]

    # Find all bridges for these KBs
    bridges = (
        session.query(AgentKnowledgeBaseBridge)
        .filter(AgentKnowledgeBaseBridge.kb_id.in_(kb_ids))
        .all()
    )

    agent_ids_to_sync = list(set([b.agent_id for b in bridges]))

    # Deleting bridge records auto-unbinds them from agents in DB
    for bridge in bridges:
        session.delete(bridge)

    logger.info(
        f"_enforce_kb_size_limits | user={user_id} | "
        f"unbound_kb_ids={kb_ids} | agent_ids_to_sync={agent_ids_to_sync}"
    )

    return {
        "unbound_kb_ids": kb_ids,
        "agent_ids_to_sync": agent_ids_to_sync,
    }