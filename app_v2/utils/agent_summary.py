"""
Per-agent summary used by both the user analytics page and the admin
user-detail 'Agents' tab: for each of a user's agents, the number of web-agent
pages, widgets, total conversations, and success / failed call counts.

Must be called inside an active db() session block.
"""
from typing import List

from fastapi_sqlalchemy import db
from sqlalchemy import func, case

from app_v2.databases.models import (
    AgentModel,
    ConversationsModel,
    WidgetModel,
    WebAgentPageModel,
)
from app_v2.schemas.enum_types import CallStatusEnum
from app_v2.schemas.user_dashboard import AgentSummaryItem


def build_agent_summaries(user_id: int) -> List[AgentSummaryItem]:
    # Conversation aggregates per agent (total + success/failed) — one row/agent.
    conv_sub = (
        db.session.query(
            ConversationsModel.agent_id.label("agent_id"),
            func.count(ConversationsModel.id).label("total"),
            func.sum(
                case((ConversationsModel.call_status == CallStatusEnum.success, 1), else_=0)
            ).label("success"),
            func.sum(
                case((ConversationsModel.call_status == CallStatusEnum.failed, 1), else_=0)
            ).label("failed"),
            # Total coins actually charged to the user for this agent's calls.
            func.coalesce(func.sum(ConversationsModel.coins_charged_to_user), 0).label("credits_used"),
        )
        .group_by(ConversationsModel.agent_id)
        .subquery()
    )
    # Widget / web-agent counts live on separate tables — count them as their own
    # grouped subqueries to avoid join fan-out corrupting the conversation counts.
    widget_sub = (
        db.session.query(
            WidgetModel.agent_id.label("agent_id"),
            func.count(WidgetModel.id).label("cnt"),
        )
        .group_by(WidgetModel.agent_id)
        .subquery()
    )
    webagent_sub = (
        db.session.query(
            WebAgentPageModel.agent_id.label("agent_id"),
            func.count(WebAgentPageModel.id).label("cnt"),
        )
        .group_by(WebAgentPageModel.agent_id)
        .subquery()
    )

    rows = (
        db.session.query(
            AgentModel.id,
            AgentModel.agent_name,
            AgentModel.elevenlabs_agent_id,
            func.coalesce(conv_sub.c.total, 0),
            func.coalesce(conv_sub.c.success, 0),
            func.coalesce(conv_sub.c.failed, 0),
            func.coalesce(widget_sub.c.cnt, 0),
            func.coalesce(webagent_sub.c.cnt, 0),
            func.coalesce(conv_sub.c.credits_used, 0),
        )
        .outerjoin(conv_sub, AgentModel.id == conv_sub.c.agent_id)
        .outerjoin(widget_sub, AgentModel.id == widget_sub.c.agent_id)
        .outerjoin(webagent_sub, AgentModel.id == webagent_sub.c.agent_id)
        .filter(AgentModel.user_id == user_id)
        .order_by(AgentModel.agent_name)
        .all()
    )

    return [
        AgentSummaryItem(
            agent_id=r[0],
            agent_name=r[1],
            elevenlabs_agent_id=r[2],
            total_conversations=int(r[3] or 0),
            success_count=int(r[4] or 0),
            failed_count=int(r[5] or 0),
            widget_count=int(r[6] or 0),
            web_agent_count=int(r[7] or 0),
            total_credits_used=int(r[8] or 0),
        )
        for r in rows
    ]
