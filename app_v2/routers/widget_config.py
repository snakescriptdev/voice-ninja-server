from typing import Optional
from fastapi import Body

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import Response, HTMLResponse

from fastapi_sqlalchemy import db
from sqlalchemy.orm import selectinload
from sqlalchemy import func

from app_v2.databases.models import AgentModel, AgentLanguageBridge, WidgetModel, UnifiedAuthModel
from app_v2.schemas.widget_schema import WidgetConfig, WidgetConfigResponse, WidgetListResponse, WidgetConfigUpdate
from app_v2.schemas.pagination import PaginatedResponse, PageSize
from sqlalchemy.exc import NoResultFound
from app_v2.utils.activity_logger import log_activity
import uuid
from fastapi import Depends
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer
from app_v2.utils.feature_access import RequireFeature, check_can_enable_resource
from app_v2.core.logger import setup_logger
from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY

logger = setup_logger(__name__)
security = HTTPBearer()

router = APIRouter(
    prefix="/api/v2/widget",
    tags=["widget"],
    dependencies=[Depends(security)]
)






@router.get("/widgets", response_model=PaginatedResponse[WidgetListResponse], openapi_extra={"security": [{"BearerAuth": []}]})
def list_widgets(
    request: Request,
    agent_id: Optional[int] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: PageSize = 10,
    user=Depends(require_active_user()),
):
    query = db.session.query(WidgetModel).filter(WidgetModel.user_id == user.id)
    if agent_id is not None:
        query = query.filter(WidgetModel.agent_id == agent_id)
    if search:
        query = query.filter(func.lower(WidgetModel.widget_name).contains(search.lower()))
    total = query.count()
    widgets = query.order_by(WidgetModel.created_at.desc()).offset((page - 1) * size).limit(size).all()
    base_url = str(request.base_url).rstrip("/")
    total_pages = (total + size - 1) // size if size > 0 else 1
    return PaginatedResponse(
        total=total,
        page=page,
        size=size,
        pages=total_pages,
        items=[
            WidgetListResponse(
                id=wa.id,
                widget_name=wa.widget_name,
                public_id=wa.public_id,
                shareable_link=f"{base_url}/api/v2/widget/preview/{wa.public_id}",
                is_enabled=wa.is_enabled,
                created_at = wa.created_at,
                updated_at = wa.modified_at,
                agent_id=wa.agent_id,
                agent_name=wa.agent.agent_name if wa.agent else ""
            ) for wa in widgets
        ],
    )


@router.post("/widgets", response_model=WidgetConfigResponse,openapi_extra={"security":[{"BearerAuth":[]}]})
def create_widget(request: Request, config: WidgetConfig, user=Depends(RequireFeature("widget_agent", allow_coin_fallback=True))):
  # Validate agent belongs to user
  agent = db.session.query(AgentModel).filter(AgentModel.id == config.agent_id, AgentModel.user_id == user.id).first()
  if not agent:
    raise HTTPException(status_code=403, detail="Agent does not belong to user")
  if not agent.is_enabled:
        raise HTTPException(status_code=403,detail="agent is disabled")
  widget = db.session.query(WidgetModel).filter(
    WidgetModel.agent_id == config.agent_id,
    func.lower(WidgetModel.widget_name) == config.widget_name.lower()
  ).first()
  if widget:
    raise HTTPException(status_code=400, detail="Widget with same name already exists for this Voice Agent.")

  public_id = str(uuid.uuid4())
  widget = WidgetModel(
    public_id=public_id,
    user_id=user.id,
    agent_id=config.agent_id,
    widget_name=config.widget_name,
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
  db.session.add(widget)
  db.session.commit()
  
  log_activity(
      user_id=user.id,
      event_type="widget_created",
      description=f"Created widget: {widget.widget_name}",
      metadata={"widget_id": widget.id, "public_id": widget.public_id}
  )

  base_url = str(request.base_url).rstrip("/")
  shareable_link = f"{base_url}/api/v2/widget/preview/{public_id}"
  return WidgetConfigResponse(
    id=widget.id,
    public_id=widget.public_id,
    widget_name=widget.widget_name,
    shareable_link=shareable_link,
    agent_id=agent.id,
    is_enabled=widget.is_enabled,
    appearance=config.appearance,
    prechat=config.prechat,
  )


@router.get("/widgets/{public_id}", response_model=WidgetConfigResponse,openapi_extra={"security":[{"BearerAuth":[]}]})
def get_widget(request: Request, public_id: str, user=Depends(require_active_user())):
  widget = db.session.query(WidgetModel).filter(WidgetModel.public_id == public_id).first()
  if not widget or widget.user_id != user.id:
    raise HTTPException(status_code=404, detail="Widget not found or not owned by user")
  agent = db.session.query(AgentModel).filter(AgentModel.id == widget.agent_id).first()
  # Map DB to schema
  appearance = {
    "widget_title": widget.widget_title,
    "widget_subtitle": widget.widget_subtitle,
    "primary_color": widget.primary_color,
    "position": widget.position,
    "show_branding": widget.show_branding,
  }
  prechat = {
    "enable_prechat": widget.enable_prechat,
    "require_name": widget.require_name,
    "require_email": widget.require_email,
    "require_phone": widget.require_phone,
    "custom_fields": widget.custom_fields or [],
  }
  base_url = str(request.base_url).rstrip("/")
  shareable_link = f"{base_url}/api/v2/widget/preview/{widget.public_id}"
  return WidgetConfigResponse(
    id=widget.id,
    public_id=widget.public_id,
    widget_name=widget.widget_name,
    shareable_link=shareable_link,
    agent_id=agent.id if agent else "",
    is_enabled=widget.is_enabled,
    appearance=appearance,
    prechat=prechat,
  )

@router.put(
    "/widgets/{public_id}",
    response_model=WidgetConfigResponse,
    openapi_extra={"security": [{"BearerAuth": []}]},
)
def update_widget(
    request: Request,
    public_id: str,
    config: WidgetConfigUpdate = Body(...),
    user=Depends(require_active_user()),
):
    # Fetch Widget
    widget = (
        db.session.query(WidgetModel)
        .filter(WidgetModel.public_id == public_id)
        .first()
    )

    if not widget or widget.user_id != user.id:
        raise HTTPException(status_code=404, detail="Widget not found or not owned by user")

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

        widget.agent_id = update_data["agent_id"]

    # ------------------ Basic Fields ------------------
    if "widget_name" in update_data:
        widget.widget_name = update_data["widget_name"]
    if "is_enabled" in update_data:
        if update_data["is_enabled"] and not widget.is_enabled:
            voice_agent = (
                db.session.query(AgentModel)
                .filter(AgentModel.id == widget.agent_id)
                .first()
            )
            if not voice_agent or not voice_agent.is_enabled:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot enable widget: its Voice Agent is disabled",
                )
            check_can_enable_resource(user.id, "widget_agent", allow_coin_fallback=True)
        widget.is_enabled = update_data["is_enabled"]

    # ------------------ Appearance Update ------------------
    if "appearance" in update_data:
        appearance_data = update_data["appearance"]

        if "widget_title" in appearance_data:
            widget.widget_title = appearance_data["widget_title"]

        if "widget_subtitle" in appearance_data:
            widget.widget_subtitle = appearance_data["widget_subtitle"]

        if "primary_color" in appearance_data:
            widget.primary_color = appearance_data["primary_color"]

        if "position" in appearance_data:
            widget.position = appearance_data["position"]

        if "show_branding" in appearance_data:
            widget.show_branding = appearance_data["show_branding"]

    # ------------------ Prechat Update ------------------
    if "prechat" in update_data:
        prechat_data = update_data["prechat"]

        if "enable_prechat" in prechat_data:
            widget.enable_prechat = prechat_data["enable_prechat"]

        if "require_name" in prechat_data:
            widget.require_name = prechat_data["require_name"]

        if "require_email" in prechat_data:
            widget.require_email = prechat_data["require_email"]

        if "require_phone" in prechat_data:
            widget.require_phone = prechat_data["require_phone"]

        if "custom_fields" in prechat_data:
            widget.custom_fields = prechat_data["custom_fields"] or []

    db.session.commit()
    db.session.refresh(widget)

    # ------------------ Fetch Current Agent ------------------
    agent = (
        db.session.query(AgentModel)
        .filter(AgentModel.id == widget.agent_id)
        .first()
    )

    base_url = str(request.base_url).rstrip("/")
    shareable_link = f"{base_url}/api/v2/widget/preview/{widget.public_id}"

    appearance = {
        "widget_title": widget.widget_title,
        "widget_subtitle": widget.widget_subtitle,
        "primary_color": widget.primary_color,
        "position": widget.position,
        "show_branding": widget.show_branding,
    }

    prechat = {
        "enable_prechat": widget.enable_prechat,
        "require_name": widget.require_name,
        "require_email": widget.require_email,
        "require_phone": widget.require_phone,
        "custom_fields": widget.custom_fields or [],
    }

    log_activity(
      user_id=user.id,
      event_type="widget_updated",
      description=f"Created widget: {widget.widget_name}",
      metadata={"widget_id": widget.id, "public_id": widget.public_id}
  )

    return WidgetConfigResponse(
        id=widget.id,
        public_id=widget.public_id,
        widget_name=widget.widget_name,
        shareable_link=shareable_link,
        agent_id=agent.id if agent else None,
        is_enabled=widget.is_enabled,
        appearance=appearance,
        prechat=prechat,
    )

@router.delete("/widgets/{public_id}",openapi_extra={"security":[{"BearerAuth":[]}]})
def delete_widget(public_id: str, user=Depends(require_active_user())):
  widget = db.session.query(WidgetModel).filter(WidgetModel.public_id == public_id).first()
  if not widget or widget.user_id != user.id:
    raise HTTPException(status_code=404, detail="Widget not found or not owned by user")
  db.session.delete(widget)
  db.session.commit()
  return {"detail": "Widget deleted"}




