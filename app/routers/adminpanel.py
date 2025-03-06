from fastapi import APIRouter,Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, FileResponse, Response, HTMLResponse    
from app.databases.models import AgentModel, KnowledgeBaseModel, agent_knowledge_association, UserModel, AgentConnectionModel
from sqlalchemy.orm import sessionmaker
from app.databases.models import engine
import os

router = APIRouter(prefix="/admin")

templates = Jinja2Templates(directory="templates")

@router.get("/", name="admin_login")
async def admin_login(request: Request):
    if request.session.get("is_admin"):
        return RedirectResponse(url="/admin/admin_dashboard")
    return templates.TemplateResponse(
        "Adminpanel/login.html", 
        {
            "request": request,
        }
    )


@router.get("/admin_dashboard", name="admin_dashboard")
async def admin_dashboard(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/admin/admin_login")
    return templates.TemplateResponse(
        "Adminpanel/dashboard.html", 
        {"request": request}
    )


@router.get("/admin_logout", name="admin_logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/")


@router.get("/admin_signup", name="admin_signup")
async def admin_signup(request: Request):
    if request.session.get("is_admin"):
        return RedirectResponse(url="/admin/admin_dashboard")
    return templates.TemplateResponse(      
        "Adminpanel/signup.html", {"request": request}
    )   
