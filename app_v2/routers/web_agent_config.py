import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from fastapi_sqlalchemy import db
from sqlalchemy import func

from app_v2.databases.models import AgentModel, WidgetModel, WebAgentPageModel
from app_v2.schemas.web_agent_schema import (
    WebAgentCreate,
    WebAgentUpdate,
    WebAgentResponse,
    WebAgentListResponse,
)
from app_v2.utils.activity_logger import log_activity
from app_v2.utils.jwt_utils import HTTPBearer
from app_v2.utils.feature_access import RequireFeatureEnabled
from app_v2.schemas.enum_types import PlanFeatureEnum
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(
    prefix="/api/v2/web-agents",
    tags=["web-agent"],
    dependencies=[Depends(security)],
)


def _shareable_link(request: Request, public_id: str) -> str:
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/v2/web-agents/{public_id}/web-agent"


def _to_response(request: Request, web_agent: WebAgentPageModel) -> WebAgentResponse:
    return WebAgentResponse(
        id=web_agent.id,
        public_id=web_agent.public_id,
        web_agent_name=web_agent.web_agent_name,
        agent_id=web_agent.agent_id,
        agent_name=web_agent.agent.agent_name if web_agent.agent else "",
        widget_id=web_agent.widget_id,
        widget_name=web_agent.widget.widget_name if web_agent.widget else "",
        is_enabled=web_agent.is_enabled,
        bg_color=web_agent.bg_color,
        agent_position=web_agent.agent_position,
        shareable_link=_shareable_link(request, web_agent.public_id),
        created_at=web_agent.created_at,
    )


def _validate_widget_belongs_to_agent(user_id: int, widget_id: int, agent_id: int) -> WidgetModel:
    widget = db.session.query(WidgetModel).filter(
        WidgetModel.id == widget_id,
        WidgetModel.user_id == user_id,
    ).first()
    if not widget:
        raise HTTPException(status_code=403, detail="Widget does not belong to user")
    if widget.agent_id != agent_id:
        raise HTTPException(status_code=400, detail="Widget does not belong to the selected agent")
    return widget


def _check_web_agent_name_unique(
    agent_id: int,
    widget_id: int,
    web_agent_name: str,
    exclude_id: int | None = None,
) -> None:
    """
    A web agent name only needs to be unique within the same (voice agent,
    widget) pair — the same name is fine under a different agent or a
    different widget.
    """
    query = db.session.query(WebAgentPageModel).filter(
        WebAgentPageModel.agent_id == agent_id,
        WebAgentPageModel.widget_id == widget_id,
        func.lower(WebAgentPageModel.web_agent_name) == web_agent_name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(WebAgentPageModel.id != exclude_id)
    if query.first():
        raise HTTPException(
            status_code=400,
            detail="A web agent with this name already exists for this Voice Agent and Widget combination.",
        )


@router.get("/", response_model=list[WebAgentListResponse], openapi_extra={"security": [{"BearerAuth": []}]})
def list_web_agents(request: Request, user=Depends(RequireFeatureEnabled(PlanFeatureEnum.web_agent))):
    web_agents = (
        db.session.query(WebAgentPageModel)
        .filter(WebAgentPageModel.user_id == user.id)
        .order_by(WebAgentPageModel.created_at.desc())
        .all()
    )
    return [
        WebAgentListResponse(
            id=wa.id,
            public_id=wa.public_id,
            web_agent_name=wa.web_agent_name,
            is_enabled=wa.is_enabled,
            bg_color=wa.bg_color,
            agent_position=wa.agent_position,
            agent_id=wa.agent_id,
            agent_name=wa.agent.agent_name if wa.agent else "",
            widget_id=wa.widget_id,
            widget_name=wa.widget.widget_name if wa.widget else "",
            shareable_link=_shareable_link(request, wa.public_id),
            created_at=wa.created_at,
        )
        for wa in web_agents
    ]


@router.post("/", response_model=WebAgentResponse, openapi_extra={"security": [{"BearerAuth": []}]})
def create_web_agent(request: Request, payload: WebAgentCreate, user=Depends(RequireFeatureEnabled(PlanFeatureEnum.web_agent))):
    agent = db.session.query(AgentModel).filter(
        AgentModel.id == payload.agent_id,
        AgentModel.user_id == user.id,
    ).first()
    if not agent:
        raise HTTPException(status_code=403, detail="Agent does not belong to user")
    if not agent.is_enabled:
        raise HTTPException(status_code=403, detail="Agent is disabled")

    _validate_widget_belongs_to_agent(user.id, payload.widget_id, payload.agent_id)
    _check_web_agent_name_unique(payload.agent_id, payload.widget_id, payload.web_agent_name)

    web_agent = WebAgentPageModel(
        public_id=str(uuid.uuid4()),
        user_id=user.id,
        agent_id=payload.agent_id,
        widget_id=payload.widget_id,
        web_agent_name=payload.web_agent_name,
        bg_color=payload.bg_color,
        agent_position=payload.agent_position,
    )
    db.session.add(web_agent)
    db.session.commit()
    db.session.refresh(web_agent)

    log_activity(
        user_id=user.id,
        event_type="web_agent_created",
        description=f"Created web agent: {web_agent.web_agent_name}",
        metadata={"web_agent_id": web_agent.id, "public_id": web_agent.public_id},
    )

    return _to_response(request, web_agent)


@router.get("/{public_id}", response_model=WebAgentResponse, openapi_extra={"security": [{"BearerAuth": []}]})
def get_web_agent(request: Request, public_id: str, user=Depends(RequireFeatureEnabled(PlanFeatureEnum.web_agent))):
    web_agent = db.session.query(WebAgentPageModel).filter(WebAgentPageModel.public_id == public_id).first()
    if not web_agent or web_agent.user_id != user.id:
        raise HTTPException(status_code=404, detail="Web agent not found or not owned by user")
    return _to_response(request, web_agent)


@router.put("/{public_id}", response_model=WebAgentResponse, openapi_extra={"security": [{"BearerAuth": []}]})
def update_web_agent(
    request: Request,
    public_id: str,
    payload: WebAgentUpdate = Body(...),
    user=Depends(RequireFeatureEnabled(PlanFeatureEnum.web_agent)),
):
    web_agent = db.session.query(WebAgentPageModel).filter(WebAgentPageModel.public_id == public_id).first()
    if not web_agent or web_agent.user_id != user.id:
        raise HTTPException(status_code=404, detail="Web agent not found or not owned by user")

    update_data = payload.model_dump(exclude_unset=True)

    new_agent_id = update_data.get("agent_id", web_agent.agent_id)
    if "agent_id" in update_data:
        agent = db.session.query(AgentModel).filter(
            AgentModel.id == new_agent_id,
            AgentModel.user_id == user.id,
        ).first()
        if not agent:
            raise HTTPException(status_code=403, detail="Agent does not belong to user")
        web_agent.agent_id = new_agent_id

    new_widget_id = update_data.get("widget_id", web_agent.widget_id)
    if "widget_id" in update_data:
        _validate_widget_belongs_to_agent(user.id, new_widget_id, new_agent_id)
        web_agent.widget_id = new_widget_id
    elif "agent_id" in update_data:
        # Agent changed but widget wasn't re-specified — the existing widget must
        # still belong to the (new) agent for the record to stay consistent.
        _validate_widget_belongs_to_agent(user.id, new_widget_id, new_agent_id)

    new_name = update_data.get("web_agent_name", web_agent.web_agent_name)
    if "web_agent_name" in update_data or "agent_id" in update_data or "widget_id" in update_data:
        _check_web_agent_name_unique(new_agent_id, new_widget_id, new_name, exclude_id=web_agent.id)

    if "web_agent_name" in update_data:
        web_agent.web_agent_name = update_data["web_agent_name"]
    if "bg_color" in update_data:
        web_agent.bg_color = update_data["bg_color"]
    if "agent_position" in update_data:
        web_agent.agent_position = update_data["agent_position"]
    if "is_enabled" in update_data:
        if update_data["is_enabled"] and not web_agent.is_enabled:
            voice_agent = (
                db.session.query(AgentModel)
                .filter(AgentModel.id == web_agent.agent_id)
                .first()
            )
            if not voice_agent or not voice_agent.is_enabled:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot enable web agent: its Voice Agent is disabled",
                )
        web_agent.is_enabled = update_data["is_enabled"]

    db.session.commit()
    db.session.refresh(web_agent)

    log_activity(
        user_id=user.id,
        event_type="web_agent_updated",
        description=f"Updated web agent: {web_agent.web_agent_name}",
        metadata={"web_agent_id": web_agent.id, "public_id": web_agent.public_id},
    )

    return _to_response(request, web_agent)


@router.delete("/{public_id}", openapi_extra={"security": [{"BearerAuth": []}]})
def delete_web_agent(public_id: str, user=Depends(RequireFeatureEnabled(PlanFeatureEnum.web_agent))):
    web_agent = db.session.query(WebAgentPageModel).filter(WebAgentPageModel.public_id == public_id).first()
    if not web_agent or web_agent.user_id != user.id:
        raise HTTPException(status_code=404, detail="Web agent not found or not owned by user")
    db.session.delete(web_agent)
    db.session.commit()
    return {"detail": "Web agent deleted"}
