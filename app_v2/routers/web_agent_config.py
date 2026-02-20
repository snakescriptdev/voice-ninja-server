from typing import Optional
from fastapi import Body

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import Response, HTMLResponse

from fastapi_sqlalchemy import db
from sqlalchemy.orm import selectinload

from app_v2.databases.models import AgentModel, AgentLanguageBridge, WebAgentModel, UnifiedAuthModel
from app_v2.schemas.web_agent_schema import WebAgentConfig, WebAgentConfigResponse, WebAgentListResponse, WebAgentConfigUpdate
from sqlalchemy.exc import NoResultFound
from app_v2.utils.activity_logger import log_activity
import uuid
from fastapi import Depends
from app_v2.utils.jwt_utils import get_current_user, HTTPBearer
from app_v2.core.logger import setup_logger
from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(
    prefix="/api/v2/web-agent",
    tags=["web-agent"],
    dependencies=[Depends(security)]
)






@router.get("/web-agents", response_model=list[WebAgentListResponse], openapi_extra={"security": [{"BearerAuth": []}]})
def list_web_agents(request: Request, user=Depends(get_current_user)):
    web_agents = db.session.query(WebAgentModel).filter(WebAgentModel.user_id == user.id).all()
    base_url = str(request.base_url).rstrip("/")
    return [
        WebAgentListResponse(
            id=wa.id,
            web_agent_name=wa.web_agent_name,
            public_id=wa.public_id,
            shareable_link=f"{base_url}/api/v2/web-agent/preview/{wa.public_id}",
            is_enabled=wa.is_enabled
        ) for wa in web_agents
    ]


@router.post("/web-agents", response_model=WebAgentConfigResponse,openapi_extra={"security":[{"BearerAuth":[]}]})
def create_web_agent(request: Request, config: WebAgentConfig, user=Depends(get_current_user)):
  # Validate agent belongs to user
  agent = db.session.query(AgentModel).filter(AgentModel.id == config.agent_id, AgentModel.user_id == user.id).first()
  if not agent:
    raise HTTPException(status_code=403, detail="Agent does not belong to user")

  public_id = str(uuid.uuid4())
  web_agent = WebAgentModel(
    public_id=public_id,
    user_id=user.id,
    agent_id=config.agent_id,
    web_agent_name=config.web_agent_name,
    widget_title=config.appearance.widget_title,
    widget_subtitle=config.appearance.widget_subtitle,
    primary_color=config.appearance.primary_color,
    position=config.appearance.position,
    show_branding=config.appearance.show_branding,
    enable_prechat=config.prechat.enable_prechat,
    require_name=config.prechat.require_name,
    require_email=config.prechat.require_email,
    require_phone=config.prechat.require_phone,
    custom_fields=[field.model_dump() for field in config.prechat.custom_fields] if config.prechat.custom_fields else [],
  )
  db.session.add(web_agent)
  db.session.commit()
  
  log_activity(
      user_id=user.id,
      event_type="web_agent_created",
      description=f"Created web agent: {web_agent.web_agent_name}",
      metadata={"web_agent_id": web_agent.id, "public_id": web_agent.public_id}
  )

  base_url = str(request.base_url).rstrip("/")
  shareable_link = f"{base_url}/api/v2/web-agent/preview/{public_id}"
  return WebAgentConfigResponse(
    id=web_agent.id,
    public_id=web_agent.public_id,
    web_agent_name=web_agent.web_agent_name,
    shareable_link=shareable_link,
    agent_name=agent.agent_name,
    is_enabled=web_agent.is_enabled,
    appearance=config.appearance,
    prechat=config.prechat,
  )


@router.get("/web-agents/{public_id}", response_model=WebAgentConfigResponse,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_web_agent(request: Request, public_id: str, user=Depends(get_current_user)):
  web_agent = db.session.query(WebAgentModel).filter(WebAgentModel.public_id == public_id).first()
  if not web_agent or web_agent.user_id != user.id:
    raise HTTPException(status_code=404, detail="WebAgent not found or not owned by user")
  agent = db.session.query(AgentModel).filter(AgentModel.id == web_agent.agent_id).first()
  # Map DB to schema
  appearance = {
    "widget_title": web_agent.widget_title,
    "widget_subtitle": web_agent.widget_subtitle,
    "primary_color": web_agent.primary_color,
    "position": web_agent.position,
    "show_branding": web_agent.show_branding,
  }
  prechat = {
    "enable_prechat": web_agent.enable_prechat,
    "require_name": web_agent.require_name,
    "require_email": web_agent.require_email,
    "require_phone": web_agent.require_phone,
    "custom_fields": web_agent.custom_fields or [],
  }
  base_url = str(request.base_url).rstrip("/")
  shareable_link = f"{base_url}/api/v2/web-agent/preview/{web_agent.public_id}"
  return WebAgentConfigResponse(
    id=web_agent.id,
    public_id=web_agent.public_id,
    web_agent_name=web_agent.web_agent_name,
    shareable_link=shareable_link,
    agent_name=agent.agent_name if agent else "",
    is_enabled=web_agent.is_enabled,
    appearance=appearance,
    prechat=prechat,
  )

@router.put(
    "/web-agents/{public_id}",
    response_model=WebAgentConfigResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def update_web_agent(
    request: Request,
    public_id: str,
    config: WebAgentConfigUpdate = Body(...),
    user=Depends(get_current_user),
):
    # Fetch WebAgent
    web_agent = (
        db.session.query(WebAgentModel)
        .filter(WebAgentModel.public_id == public_id)
        .first()
    )

    if not web_agent or web_agent.user_id != user.id:
        raise HTTPException(status_code=404, detail="WebAgent not found or not owned by user")

    update_data = config.model_dump(exclude_unset=True)

    # ------------------ Agent Update ------------------
    if "agent_id" in update_data:
        agent = (
            db.session.query(AgentModel)
            .filter(
                AgentModel.id == update_data["agent_id"],
                AgentModel.user_id == user.id,
            )
            .first()
        )
        if not agent:
            raise HTTPException(status_code=403, detail="Agent does not belong to user")

        web_agent.agent_id = update_data["agent_id"]

    # ------------------ Basic Fields ------------------
    if "web_agent_name" in update_data:
        web_agent.web_agent_name = update_data["web_agent_name"]
    if "is_enabled" in update_data:
        web_agent.is_enabled = update_data["is_enabled"]

    # ------------------ Appearance Update ------------------
    if "appearance" in update_data:
        appearance_data = update_data["appearance"]

        if "widget_title" in appearance_data:
            web_agent.widget_title = appearance_data["widget_title"]

        if "widget_subtitle" in appearance_data:
            web_agent.widget_subtitle = appearance_data["widget_subtitle"]

        if "primary_color" in appearance_data:
            web_agent.primary_color = appearance_data["primary_color"]

        if "position" in appearance_data:
            web_agent.position = appearance_data["position"]

        if "show_branding" in appearance_data:
            web_agent.show_branding = appearance_data["show_branding"]

    # ------------------ Prechat Update ------------------
    if "prechat" in update_data:
        prechat_data = update_data["prechat"]

        if "enable_prechat" in prechat_data:
            web_agent.enable_prechat = prechat_data["enable_prechat"]

        if "require_name" in prechat_data:
            web_agent.require_name = prechat_data["require_name"]

        if "require_email" in prechat_data:
            web_agent.require_email = prechat_data["require_email"]

        if "require_phone" in prechat_data:
            web_agent.require_phone = prechat_data["require_phone"]

        if "custom_fields" in prechat_data:
            web_agent.custom_fields = prechat_data["custom_fields"] or []

    db.session.commit()
    db.session.refresh(web_agent)

    # ------------------ Fetch Current Agent ------------------
    agent = (
        db.session.query(AgentModel)
        .filter(AgentModel.id == web_agent.agent_id)
        .first()
    )

    base_url = str(request.base_url).rstrip("/")
    shareable_link = f"{base_url}/api/v2/web-agent/preview/{web_agent.public_id}"

    appearance = {
        "widget_title": web_agent.widget_title,
        "widget_subtitle": web_agent.widget_subtitle,
        "primary_color": web_agent.primary_color,
        "position": web_agent.position,
        "show_branding": web_agent.show_branding,
    }

    prechat = {
        "enable_prechat": web_agent.enable_prechat,
        "require_name": web_agent.require_name,
        "require_email": web_agent.require_email,
        "require_phone": web_agent.require_phone,
        "custom_fields": web_agent.custom_fields or [],
    }

    log_activity(
      user_id=user.id,
      event_type="web_agent_updated",
      description=f"Created web agent: {web_agent.web_agent_name}",
      metadata={"web_agent_id": web_agent.id, "public_id": web_agent.public_id}
  )

    return WebAgentConfigResponse(
        id=web_agent.id,
        public_id=web_agent.public_id,
        web_agent_name=web_agent.web_agent_name,
        shareable_link=shareable_link,
        agent_name=agent.agent_name if agent else None,
        is_enabled=web_agent.is_enabled,
        appearance=appearance,
        prechat=prechat,
    )

@router.delete("/web-agents/{public_id}",openapi_extra={"security":[{"BearerAuth":[]}]})
def delete_web_agent(public_id: str, user=Depends(get_current_user)):
  web_agent = db.session.query(WebAgentModel).filter(WebAgentModel.public_id == public_id).first()
  if not web_agent or web_agent.user_id != user.id:
    raise HTTPException(status_code=404, detail="WebAgent not found or not owned by user")
  db.session.delete(web_agent)
  db.session.commit()
  return {"detail": "WebAgent deleted"}




