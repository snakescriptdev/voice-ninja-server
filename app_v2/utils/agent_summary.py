"""
Per-agent summary used by both the user analytics page and the admin
user-detail 'Agents' tab: for each of a user's agents, the number of web-agent
pages, widgets, total conversations, success / failed call counts, knowledge
base / tool counts, and total KB pages.

Must be called inside an active db() session block.
"""
from typing import List, Optional

from fastapi_sqlalchemy import db
from sqlalchemy import func, case

from app_v2.databases.models import (
    AgentModel,
    ConversationsModel,
    WidgetModel,
    WebAgentPageModel,
    AgentKnowledgeBaseBridge,
    AgentFunctionBridgeModel,
)
from app_v2.schemas.enum_types import CallStatusEnum
from app_v2.schemas.user_dashboard import AgentSummaryItem, WebAgentSummaryRef, WidgetSummaryRef

# sort_by values accepted by build_agent_summaries — anything else (including
# None) falls back to the default alphabetical-by-name order.
_SORT_OPTIONS = {"credits_desc", "date_added_desc", "kb_count_desc", "tool_count_desc"}


def build_agent_summaries(user_id: int, sort_by: Optional[str] = None) -> List[AgentSummaryItem]:
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
    # Knowledge base / tool counts — same "own grouped subquery" approach as
    # widget_sub/webagent_sub, to avoid join fan-out corrupting other counts.
    kb_sub = (
        db.session.query(
            AgentKnowledgeBaseBridge.agent_id.label("agent_id"),
            func.count(AgentKnowledgeBaseBridge.kb_id).label("cnt"),
        )
        .group_by(AgentKnowledgeBaseBridge.agent_id)
        .subquery()
    )
    tool_sub = (
        db.session.query(
            AgentFunctionBridgeModel.agent_id.label("agent_id"),
            func.count(AgentFunctionBridgeModel.function_id).label("cnt"),
        )
        .group_by(AgentFunctionBridgeModel.agent_id)
        .subquery()
    )

    query = (
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
            func.coalesce(kb_sub.c.cnt, 0),
            func.coalesce(tool_sub.c.cnt, 0),
            AgentModel.kb_total_pages,
        )
        .outerjoin(conv_sub, AgentModel.id == conv_sub.c.agent_id)
        .outerjoin(widget_sub, AgentModel.id == widget_sub.c.agent_id)
        .outerjoin(webagent_sub, AgentModel.id == webagent_sub.c.agent_id)
        .outerjoin(kb_sub, AgentModel.id == kb_sub.c.agent_id)
        .outerjoin(tool_sub, AgentModel.id == tool_sub.c.agent_id)
        .filter(AgentModel.user_id == user_id)
    )

    if sort_by not in _SORT_OPTIONS:
        query = query.order_by(AgentModel.agent_name)
    elif sort_by == "credits_desc":
        query = query.order_by(func.coalesce(conv_sub.c.credits_used, 0).desc(), AgentModel.agent_name)
    elif sort_by == "date_added_desc":
        query = query.order_by(AgentModel.created_at.desc(), AgentModel.agent_name)
    elif sort_by == "kb_count_desc":
        query = query.order_by(func.coalesce(kb_sub.c.cnt, 0).desc(), AgentModel.agent_name)
    elif sort_by == "tool_count_desc":
        query = query.order_by(func.coalesce(tool_sub.c.cnt, 0).desc(), AgentModel.agent_name)

    rows = query.all()

    # Actual web-agent / widget rows (id, public_id, name) per agent, so the
    # frontend can render clickable links instead of just a count.
    widgets_by_agent = {}
    for agent_id, wid, public_id, widget_name in (
        db.session.query(WidgetModel.agent_id, WidgetModel.id, WidgetModel.public_id, WidgetModel.widget_name)
        .filter(WidgetModel.agent_id.in_([r[0] for r in rows]))
        .all()
    ):
        widgets_by_agent.setdefault(agent_id, []).append(
            WidgetSummaryRef(id=wid, public_id=public_id, widget_name=widget_name)
        )

    web_agents_by_agent = {}
    for agent_id, wa_id, public_id, web_agent_name in (
        db.session.query(WebAgentPageModel.agent_id, WebAgentPageModel.id, WebAgentPageModel.public_id, WebAgentPageModel.web_agent_name)
        .filter(WebAgentPageModel.agent_id.in_([r[0] for r in rows]))
        .all()
    ):
        web_agents_by_agent.setdefault(agent_id, []).append(
            WebAgentSummaryRef(id=wa_id, public_id=public_id, web_agent_name=web_agent_name)
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
            web_agents=web_agents_by_agent.get(r[0], []),
            widgets=widgets_by_agent.get(r[0], []),
            total_credits_used=int(r[8] or 0),
            kb_count=int(r[9] or 0),
            tool_count=int(r[10] or 0),
            kb_total_pages=r[11],
        )
        for r in rows
    ]
