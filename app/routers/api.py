from fastapi import APIRouter,Request, Response
from .schemas.format import (
    ErrorResponse, 
    SuccessResponse, 
)
from app.core import logger
from app.services import AudioStorage
from starlette.responses import JSONResponse, RedirectResponse
from app.databases.models import (
    AudioRecordModel, UserModel,
    AgentModel, ResetPasswordModel, 
    AgentConnectionModel, PaymentModel, 
    AdminTokenModel, AudioRecordings, 
    TokensToConsume, ApprovedDomainModel, CallModel,
    KnowledgeBaseModel, KnowledgeBaseFileModel, WebhookModel, 
    CustomFunctionModel,ConversationModel, DailyCallLimitModel,
    OverallTokenLimitModel
    )
from app.databases.schema import  AudioRecordListSchema
import json, re
import bcrypt
from app.utils.helper import make_outbound_call, generate_twiml
from fastapi_mail import FastMail, MessageSchema,ConnectionConfig
import os
import uuid
from pydantic import BaseModel
from datetime import datetime
import shutil
import json
from app.utils.helper import extract_text_from_file, generate_agent_prompt
from config import MEDIA_DIR  # ✅ Import properly
import razorpay
from app.utils.helper import verify_razorpay_signature
from jinja2 import Environment, meta
from app.utils.helper import generate_summary,is_valid_url
from app.utils.scrap import scrape_and_get_file
from app.utils.langchain_integration import get_splits, convert_to_vectorstore
import asyncio
from fastapi_sqlalchemy import db
from app.validators.api_validators import SaveNoiseVariablesRequest,ResetNoiseVariablesRequest
from pydantic import ValidationError
from app.core.config import DEFAULT_VARS,NOISE_SETTINGS_DESCRIPTIONS
router = APIRouter(prefix="/api")

razorpay_client = razorpay.Client(auth=(os.getenv("RAZOR_KEY_ID"), os.getenv("RAZOR_KEY_SECRET")))

class EmailSettings(BaseModel):
    MAIL_USERNAME: str = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD")
    MAIL_FROM: str = os.getenv("MAIL_FROM")
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    USE_CREDENTIALS: bool = True

email_settings = EmailSettings()

conf = ConnectionConfig(
    MAIL_USERNAME=email_settings.MAIL_USERNAME,
    MAIL_PASSWORD=email_settings.MAIL_PASSWORD,
    MAIL_FROM=email_settings.MAIL_FROM,
    MAIL_PORT=email_settings.MAIL_PORT,
    MAIL_SERVER=email_settings.MAIL_SERVER,
    MAIL_STARTTLS=email_settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=email_settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=email_settings.USE_CREDENTIALS
)


@router.get("/heartbeat/")
async def heartbeat():
    logger.info("Heartbeat endpoint called")
    return JSONResponse(content={"message": "Voice Agent is running and ready to receive calls"})



@router.get(
    "/audio-files/",
    responses={
        500: {"model": ErrorResponse}
    }
)
async def get_audio_file_list(request: Request):
    try:
        audio_files = AudioRecordModel.get_recent_records()
        response_data = AudioRecordListSchema(audio_records=audio_files).model_dump(request=request)
        response_data['status'] = "success"
        response_data['message'] = "Audio files retrieved successfully"
        return JSONResponse(
            status_code=200,
            content=response_data
        )
    except Exception as e:
        print(e)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error retrieving audio files: {str(e)}").model_dump()
        )

@router.delete(
    "/audio-delete/{id}/",
    response_model=SuccessResponse,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def delete_audio_file(id: int):
    """Delete audio file for session"""
    try:
        audio_record = AudioRecordModel.get_by_id(id)
        if audio_record:
            AudioStorage.delete_audio(audio_record.file_name)
            audio_record.delete()
            return SuccessResponse(message="Audio file deleted successfully")
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="Audio file not found").model_dump()
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"Error deleting audio file: {str(e)}").dict()
        )
@router.post("/user-login",
    response_model=SuccessResponse,
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def user_login(request: Request, response: Response):
    try:
        # Validate request has JSON content type
        if not request.headers.get("content-type") == "application/json":
            error_response = {
                "status": "error", 
                "error": "Content-Type must be application/json",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response
            )

        data = await request.json()
        email = data.get("email")
        password = data.get("password")

        # Validate required fields
        if not all(key in data for key in ["email", "password"]):
            error_response = {
                "status": "error", 
                "error": "Missing required fields",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response
            )

        user = UserModel.get_by_email(email)
        if not user:
            error_response = {
                "status": "error", 
                "error": "User not found",
                "status_code": 401
            }
            return JSONResponse(
                status_code=401,
                content=error_response
            )
        if user and not bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
            error_response = {
                "status": "error", 
                "error": "Invalid email or password",
                "status_code": 401
            }
            return JSONResponse(
                status_code=401,
                content=error_response
            )
        if not user.is_verified:
            error_response = {
                "status": "error", 
                "error": "Your account is not verified, please check your email for verification",
                "status_code": 401
            }
            return JSONResponse(
                status_code=401,
                content=error_response
            )
        # Create session data
        session_data = {
            "user_id": user.id,
            "email": user.email,
            "name": user.name,
            "is_authenticated": True,
            "expiry": 86400,
            "created_at": datetime.now().timestamp()
        }

        # Set session cookie with encrypted data
        request.session["user"] = session_data
        UserModel.update(user.id, last_login=datetime.now())
        response_data = {
            "status": "success",
            "message": "User logged in successfully",
            "status_code": 200
        }
        return JSONResponse(
            status_code=200,
            content=response_data
        )
    except json.JSONDecodeError:
        error_response = {
            "status": "error", 
            "error": "Invalid JSON data",
            "status_code": 400
        }
        return JSONResponse(
            status_code=400,
            content=error_response
        )
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error logging in: {str(e)}",
            "status_code": 500
        }
        return JSONResponse(
            status_code=500,
            content=error_response
        )

@router.post("/user-register",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def user_register(request: Request):
    try:
        # Validate request has JSON content type
        if not request.headers.get("content-type") == "application/json":
            error_response = {
                "status": "error", 
                "error": "Content-Type must be application/json",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response
            )
            
        data = await request.json()
        
        # Validate required fields
        if not all(key in data for key in ["email", "name", "password"]):
            error_response = {
                "status": "error", 
                "error": "Missing required fields",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400, 
                content=error_response
            )
            
        email = data.get("email")
        name = data.get("name") 
        password = data.get("password")

        try:
            # Validate email format
            if not email or "@" not in email:
                error_response = {
                    "status": "error", 
                    "error": "Invalid email format",
                    "status_code": 400
                }
                return JSONResponse(
                    status_code=400,
                    content=error_response
                )

            # Use filter method instead of get_by_email
            user = UserModel.get_by_email(email)
            if user:
                error_response = {
                    "status": "error", 
                    "error": "User already exists",
                    "status_code": 400
                }
                return JSONResponse(
                    status_code=400,
                    content=error_response
                )
            email_token = uuid.uuid4()

            token_values = AdminTokenModel.get_by_id(1)

            if not token_values:
                token_values = 20
            else:
                token_values = token_values.free_tokens

            user = UserModel.create(email=email, name=name, password=password, is_verified=False, tokens=token_values)
            if not ResetPasswordModel.get_by_email(email):    
                ResetPasswordModel.create(email=email, token=email_token)
            else:
                ResetPasswordModel.update(email=email, token=email_token)
            host = request.headers.get("origin")
            template = f"""
                        <html>
                        <body>                    

                        <p>Hi {user.name} !!!
                                <br>Please click on the link below to verify your account
                                <br>
                                <a href="{ host }/verify-account/{email_token}">Verify Account</a>
                                <br>
                                <br>
                                <br>
                                <br>
                                </p>
                        </body>
                        </html>
                        """

            message = MessageSchema(
                subject="Verify Account",
                recipients=[email],
                body=template,
                subtype="html"
                )

            fm = FastMail(conf)
            await fm.send_message(message)
            response_data = {
                "status": "success",
                "message": "User registered successfully",
                "status_code": 200
            }
            return JSONResponse(
                status_code=200,
                content=response_data
            )
    
        except Exception as e:
            if user:
                UserModel.delete(user.id)
                raise e
        
    except json.JSONDecodeError:
        error_response = {
            "status": "error", 
            "error": "Invalid JSON payload",
            "status_code": 400
        }
        return JSONResponse(
            status_code=400,
            content=error_response
        )
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error registering user: {str(e)}",
            "status_code": 500
        }
        return JSONResponse(
            status_code=500,
            content=error_response
        )

@router.get("/logout",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    })
async def logout(request: Request, response: Response):
    request.session.clear()
    response.delete_cookie("session_token")
    return RedirectResponse(url="/login")

@router.post("/create_new_agent",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def create_new_agent(request: Request):
    try:
        data = await request.json()
        user_id = request.session.get("user").get("user_id")
        agent_name = data.get("agent_name")
        agent_prompt = data.get("agent_prompt")
        welcome_msg = data.get("welcome_msg")
        selected_model = data.get("selected_model")
        selected_voice = data.get("selected_voice")
        selected_language = data.get("selected_language")
        phone_number = data.get("phone_number", '+17752648387')
        selected_knowledge_base = data.get("selected_knowledge_base")
        agent = AgentModel.create(
            created_by=user_id,
            agent_name=agent_name,
            agent_prompt=agent_prompt,
            welcome_msg=welcome_msg,
            selected_model=selected_model,
            selected_voice=selected_voice,
            selected_language=selected_language,
            phone_number=phone_number
        )

        agent_connection = AgentConnectionModel.create(agent_id=agent.id)
        if selected_knowledge_base:
            from sqlalchemy.orm import sessionmaker
            from app.databases.models import engine
            from sqlalchemy import insert, select
            from app.databases.models import agent_knowledge_association
            Session = sessionmaker(bind=engine)
            session = Session() 
            try:
                # Check if the association already exists
                query = select(agent_knowledge_association).where(
                    (agent_knowledge_association.c.agent_id == agent.id) &
                    (agent_knowledge_association.c.knowledge_base_id == selected_knowledge_base)
                )
                result =  session.execute(query)
                existing_association = result.fetchone()

                if not existing_association:
                    # Insert new association if it does not exist
                    stmt = insert(agent_knowledge_association).values(
                        agent_id=agent.id,  
                        knowledge_base_id=selected_knowledge_base
                    )
                    session.execute(stmt)
                    session.commit()

            except Exception as e:
                session.rollback()
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "message": f"Error updating agent: {str(e)}", "status_code": 500}
                )
        return JSONResponse(
                status_code=200,
                content={"status": "success", "message": "Agent updated successfully", "status_code": 200}
            )
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error creating agent: {str(e)}",
            "status_code": 500
        }   
        return JSONResponse(
            status_code=500,
            content=error_response
        )

@router.post("/edit_agent",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def edit_agent(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        agent_name = data.get("agent_name")
        agent_prompt = data.get("agent_prompt")
        welcome_msg = data.get("welcome_msg")
        selected_model = data.get("selected_model")
        selected_voice = data.get("selected_voice")
        selected_language = data.get("selected_language")
        phone_number = data.get("phone_number", '+17752648387')
        selected_knowledge_base = data.get("selected_knowledge_base")

        if not agent_id:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Missing agent_id", "status_code": 400}
            )
        from sqlalchemy.orm import sessionmaker
        from app.databases.models import engine
        from sqlalchemy import insert, select, delete
        from app.databases.models import agent_knowledge_association
        Session = sessionmaker(bind=engine)
        session = Session() 
        # Update Agent Details
        session.execute(
            AgentModel.__table__.update()
            .where(AgentModel.id == agent_id)
            .values(
                agent_name=agent_name,
                agent_prompt=agent_prompt,
                welcome_msg=welcome_msg,
                selected_model=selected_model,
                selected_voice=selected_voice,
                selected_language=selected_language,
                phone_number=phone_number,
            )
        )
        session.commit()

        try:
            # Check if the association already exists
            query = select(agent_knowledge_association).where(
                agent_knowledge_association.c.agent_id == agent_id
            )
            result = session.execute(query)
            existing_association = result.fetchone()

            # If the agent has a different knowledge base, delete the old one
            if existing_association and existing_association.knowledge_base_id != selected_knowledge_base:
                delete_stmt = delete(agent_knowledge_association).where(
                    agent_knowledge_association.c.agent_id == agent_id
                )
                session.execute(delete_stmt)
                session.commit()  # Ensure deletion is applied

            if selected_knowledge_base:
                # If no association exists, insert a new one
                if not existing_association or existing_association.knowledge_base_id != selected_knowledge_base:
                    stmt = insert(agent_knowledge_association).values(
                        agent_id=agent_id, 
                        knowledge_base_id=selected_knowledge_base
                    )
                    session.execute(stmt)
                    session.commit()

            return JSONResponse(
                status_code=200,
                content={"status": "success", "message": "Agent updated successfully", "status_code": 200}
            )

        except Exception as e:
            session.rollback()
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"Error updating agent: {str(e)}", "status_code": 500}
            )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Error updating agent: {str(e)}", "status_code": 500}
        )

    

@router.post("/reset_password",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)   

async def reset_password(request: Request):
    try:
        user = request.session.get("user")
        data = await request.json()
        password = data.get("password1")
        confirm_password = data.get("password2")
        token = data.get("token")
        user = ResetPasswordModel.get_by_token(token)
        if not user:
            error_response = {
                "status": "error", 
                "error": "Invalid token",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response
            )
        if password != confirm_password:
            error_response = {
                "status": "error", 
                "error": "Passwords do not match",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response
            )
        user = UserModel.get_by_email(user.email)
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        UserModel.update(user.id, password=hashed_password.decode('utf-8'))
        response_data = {
            "status": "success",
            "message": "Password reset successfully",
            "status_code": 200
        }
        ResetPasswordModel.delete(user.email)
        request.session.clear() 
        return JSONResponse(
            status_code=200,
            content=response_data
        )
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error resetting password: {str(e)}",
            "status_code": 500
        }
        return JSONResponse(
            status_code=500,
            content=error_response
        )


@router.post("/set_new_password",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)   
async def set_new_password(request: Request):
    try:
        data = await request.json()
        user = request.session.get("user")
        old_password = data.get("old_password")
        new_password = data.get("new_password")
        confirm_password = data.get("confirm_password")
        
        if not all([old_password, new_password, confirm_password]):
            error_response = {
                "status": "error",
                "error": "All password fields are required",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response
            )
            
        user = UserModel.get_by_email(user.get("email"))
        if not user:
            error_response = {
                "status": "error", 
                "error": "User not found",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response)
                
        if not bcrypt.checkpw(old_password.encode('utf-8'), user.password.encode('utf-8')):
            error_response = {
                "status": "error", 
                "error": "Invalid old password",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response)
                
        if new_password != confirm_password:
            error_response = {
                "status": "error", 
                "error": "New password and confirm password do not match",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response)
                

         # Hash the new password before saving
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
       
        UserModel.update(
            user_id=user.id,
            password=hashed_password.decode('utf-8')
        )       
        
        response_data = {
            "status": "success",
            "message": "Password changed successfully",
            "status_code": 200
        }
        return JSONResponse(
            status_code=200,
            content=response_data
        )
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error changing password: {str(e)}",
            "status_code": 500
        }


@router.post("/send_mail")
async def send_mail(request: Request):
    try:
        data = await request.json()
        email = data.get("email")
        email_token = uuid.uuid4()
        if not email:
            error_response = {
                "status": "error", 
                "error": "Email is required",
                "status_code": 400
            }
            return JSONResponse(status_code=400, content=error_response)
        user = UserModel.get_by_email(email)
        if not user:
            error_response = {
                "status": "error", 
                "error": "User not found",
                "status_code": 400
            }
            return JSONResponse(status_code=400, content=error_response)
        if not ResetPasswordModel.get_by_email(email):    
            ResetPasswordModel.create(email=email, token=email_token)
        else:
            ResetPasswordModel.update(email=email, token=email_token)

        template = f"""
                    <html>
                    <body>                    

                    <p>Hi {user.name} !!!
                            <br>Please click on the link below to reset your password
                            <br>
                            <a href="http://localhost:8000/reset_password/{email_token}">Reset Password</a>
                            <br>
                            <br>
                            <br>
                            <br>
                            </p>
                    </body>
                    </html>
                    """

        message = MessageSchema(
            subject="Forget Password",
            recipients=[email],
            body=template,
            subtype="html"
            )

        fm = FastMail(conf)
        await fm.send_message(message)
        return JSONResponse(status_code=200, content={"message": "email has been sent","status": "success", "status_code": 200})
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error sending email: {str(e)}",
            "status_code": 500
        }
        return JSONResponse(status_code=500, content=error_response)


@router.post("/verify",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }
)
async def verify_account(request: Request):
    try:
        data = await request.json()
        token = data.get("token")
        user = ResetPasswordModel.get_by_token(token)
        if not user:
            error_response = {
                "status": "error", 
                "error": "Invalid token",
                "status_code": 400
            }
            return JSONResponse(status_code=400, content=error_response)
        user = UserModel.get_by_email(user.email)
        user = UserModel.update(user.id, is_verified=True)
        ResetPasswordModel.delete(user.email)
        response_data = {
            "status": "success",
            "message": "Account verified successfully",
            "status_code": 200
        }
        return JSONResponse(status_code=200, content=response_data)
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error verifying account: {str(e)}",
            "status_code": 500
        }

@router.delete("/delete_agent",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse}
    }       
)
async def delete_agent(request: Request):
    try:
        agent_id = request.query_params.get("agent_id")
        if not agent_id:
            error_response = {
                "status": "error", 
                "error": "Agent ID is required",
                "status_code": 400
            }
            return JSONResponse(status_code=400, content=error_response)
        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            error_response = {
                "status": "error", 
                "error": "Agent not found",
                "status_code": 400
            }
            return JSONResponse(status_code=400, content=error_response)
        agent.delete(agent_id)
        response_data = {
            "status": "success",
            "message": "Agent deleted successfully",
            "status_code": 200
        }
        return JSONResponse(status_code=200, content=response_data)
    except Exception as e:
        error_response = {
            "status": "error", 
            "error": f"Error deleting agent: {str(e)}",
            "status_code": 500
        }
        return JSONResponse(status_code=500, content=error_response)


@router.post(
    "/call_agent",
    name="call_agent",
    response_model=SuccessResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def call_agent(request: Request):
    try:
        data = await request.json()
        
        agent_id = data.get("agent_id")
        if not agent_id:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": "Agent ID is required", "status_code": 400},
            )

        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": "Agent not found", "status_code": 400},
            )
        url =  request.base_url
        user = request.session.get("user")
        user_id = user.get("user_id")
        xml = generate_twiml(agent,url, user_id)
        call = make_outbound_call(xml)
        if call:
            os.remove(xml)
            return JSONResponse(
                status_code=200,
                content={"status": "success", "message": "Agent called successfully", "status_code": 200},
            )
        else:
            return JSONResponse(
                status_code=500,
                content={"status": "error", "error": "Error calling agent", "status_code": 500},
            )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": f"Error calling agent: {str(e)}", "status_code": 500},
        )



@router.post("/upload_knowledge_base", name="upload_knowledge_base")
async def upload_knowledge_base(request: Request):
    uploaded_files = []
    try:
        data = await request.form()
        name = data.get("name")
        attachments = data.getlist("attachments[]")  # Get multiple files
        
        if not attachments:
            return JSONResponse(
                status_code=400, content={"status": "error", "message": "No files uploaded."}
            )

        allowed_extensions = {".pdf", ".docx", ".txt"}
        user = request.session.get("user")

        if len(attachments) > 5:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "You can upload a maximum of 5 files."}
            )

        # Check if Knowledge Base already exists
        knowledge_base = KnowledgeBaseModel.get_by_name(name, user.get("user_id"))

        if not knowledge_base:
            knowledge_base = KnowledgeBaseModel.create(created_by_id=user.get("user_id"), knowledge_base_name=name)

        # Process and store each file
        total_text_content = ""
        content_list = []
        for attachment in attachments:
            file_ext = os.path.splitext(attachment.filename)[1].lower()
            if file_ext not in allowed_extensions:
                return JSONResponse(
                    status_code=400, content={"status": "error", "message": f"Unsupported file type: {attachment.filename}"}
                )

            # Save file temporarily
            temp_file_path = f"knowledge_base_files/{uuid.uuid4()}_{attachment.filename}"
            file_path = os.path.join(MEDIA_DIR, temp_file_path)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(attachment.file, buffer)

            # Extract text
            text_content = extract_text_from_file(file_path)
            if not text_content.strip():
                os.remove(file_path)
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": f"No readable text found in {attachment.filename}."}
                )

            # Save file details to database
            KnowledgeBaseFileModel.create(
                knowledge_base_id=knowledge_base.id,
                file_name=attachment.filename,
                file_path=temp_file_path,
                text_content=text_content
            )
            content_list.append({
                "file_path": temp_file_path,
                "text_content": text_content
            })
            uploaded_files.append(attachment.filename)

        splits = get_splits(content_list)
        vector_id = str(uuid.uuid4())
        if splits:
            status, vector_path =convert_to_vectorstore(splits, vector_id)
            KnowledgeBaseModel.update(knowledge_base.id, vector_path=vector_path, vector_id=vector_id)
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Knowledge base and files uploaded successfully.", "uploaded_files": uploaded_files}
        )

    except Exception as e:
        knowledge_base = KnowledgeBaseModel.get_by_name(name, user.get("user_id"))
        if knowledge_base:
            files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
            for file in files:
                KnowledgeBaseFileModel.delete(file.id)
            KnowledgeBaseModel.delete(knowledge_base.id)
        # Cleanup any saved files on error
        
        for file_path in uploaded_files:
            if os.path.exists(file_path):
                os.remove(file_path)

        print("Error:", str(e))
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.delete("/delete_knowledge_base", name="delete_knowledge_base")
async def delete_knowledge_base(request: Request):
    data = await request.json()
    knowledge_base_id = data.get("knowledge_base_id")
    KnowledgeBaseModel.delete(knowledge_base_id)
    return JSONResponse(status_code=200, content={"status": "success", "message": "Knowledge base deleted successfully"})



@router.post("/save_changes", name="save_changes")
async def save_changes(request: Request):
    try:

        data = await request.json()

        required_fields = ["agent_id", "icon_url", "primary_color", "secondary_color", "pulse_color"]
        for field in required_fields:
            if not data.get(field):
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": f"Missing required field: {field}"}
                )

        agent_id = data.get("agent_id")
        icon_url = data.get("icon_url")
        primary_color = data.get("primary_color") 
        secondary_color = data.get("secondary_color")
        pulse_color = data.get("pulse_color")

        connection = AgentConnectionModel.get_by_agent_id(agent_id)
        if connection:
            AgentConnectionModel.update_connection(
                agent_id,
                icon_url=icon_url,
                primary_color=primary_color,
                secondary_color=secondary_color,
                pulse_color=pulse_color
            )
        else:
            AgentConnectionModel.create_connection(
                agent_id, 
                icon_url,
                primary_color,
                secondary_color,
                pulse_color
            )
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent connection created successfully"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Something went wrong!", "error": str(e)}
        )

@router.get("/get_agent_connection", name="get_agent_connection")
async def get_agent_connection(request: Request, agent_id: str):
    try:
        connection = AgentConnectionModel.get_by_agent_id(agent_id)
        if connection:
            connection_data = {
                "icon_url": connection.icon_url,
                "primary_color": connection.primary_color,
                "secondary_color": connection.secondary_color,
                "pulse_color": connection.pulse_color,
                "start_btn_color": connection.start_btn_color
            }
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "Agent connection fetched successfully",
                    "data": connection_data
                }
            )
        else:
            return JSONResponse(    
                status_code=200,
                content={"status": "success", "message": "Agent connection not found", "data": {}}
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Something went wrong!", "error": str(e)}
        )


@router.post("/razorpay_payment", name="razorpay_payment")
async def razorpay_payment(request: Request):
    try:
        
        data = await request.json()
        amount = data.get("amount")
        
        if not amount or int(amount) < 0:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Invalid amount. Minimum amount is INR 1"}
            )
            
        currency = "INR"
        email = request.session.get("user", {}).get("email","")
        
        amount_in_paise = int(float(amount)) * 100
        
        order_data = {
            "amount": amount_in_paise,
            "currency": currency,
            "receipt": f"receipt_{amount}_{email}",
            "payment_capture": 1,  
        }
        
        order = razorpay_client.order.create(order_data)
        order_id = order.get("id")
        

        coins = int(amount_in_paise / 100)
        
        response_data = {
            "razorpay_order_id": order_id,
            "razorpay_key_id": razorpay_client.auth[0],
            "amount": amount_in_paise,
            "currency": currency,
            "description": f"Purchase of {coins} tokens",
            "email": email,
            "prefill": {
                "email": email,
            },
            "coins": coins
        }
        
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Order created successfully", "data": response_data}
        )

    except Exception as e:
        print(f"Razorpay error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Something went wrong!", "error": str(e)}
        )
    

@router.get("/razorpay_callback", name="razorpay_callback")
async def razorpay_callback(request: Request):
    try:
        payment_data = request.query_params
        razorpay_payment_id = payment_data.get("razorpay_payment_id", "")
        razorpay_order_id = payment_data.get("razorpay_order_id", "")
        razorpay_signature = payment_data.get("razorpay_signature", "")
        
        is_valid = verify_razorpay_signature(
            razorpay_order_id, 
            razorpay_payment_id, 
            razorpay_signature
        )
        
        if is_valid:
            payment = razorpay_client.payment.fetch(razorpay_payment_id)
            
            amount = int(payment["amount"]) / 100 
            email = payment.get("email", request.session.get("user", {}).get("email", ""))
            
            user = UserModel.get_by_email(email)
            if user:
                previous_tokens = user.tokens
                if previous_tokens is None:
                    previous_tokens = 0
                new_tokens = int(previous_tokens) + int(amount)
                UserModel.update_tokens(user.id, new_tokens)

                PaymentModel.create(
                    user_id=user.id,
                    order_id=razorpay_order_id,
                    payment_id=razorpay_payment_id,
                    amount=int(amount)
                )
            
            return RedirectResponse(
                url=f"/payment_success?order_id={razorpay_order_id}&amount={int(amount)}&coins={int(amount)}",
                status_code=303
            )
        else:
            return RedirectResponse(
                url="/payment_failed?message=verification_failed",
                status_code=303
            )
    
    except Exception as e:
        print(f"Razorpay callback error: {str(e)}")
        return RedirectResponse(
            url=f"/payment_failed?message=error&error={str(e)}",
            status_code=303
        )



@router.delete("/delete_audio_recording", name="delete_audio_recording")
async def delete_audio_recording(request: Request):
    try:
        audio_recording_id = request.query_params.get("audio_recording_id")
        if not audio_recording_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Audio recording ID is required"})
        AudioRecordings.delete(audio_recording_id)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Audio recording deleted successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/admin_login", name="admin_login")
async def admin_login(request: Request):
    data = await request.json()
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Email and password are required"})
    user = UserModel.get_by_email(email)
    if not user:
        return JSONResponse(status_code=400, content={"status": "error", "message": "User not found"})
    if not bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid email or password"})
    if user.is_admin == False:
        return JSONResponse(status_code=400, content={"status": "error", "message": "User is not an admin"})
    
    from datetime import datetime, timedelta
    # Create session data for 24 hours
    session = request.session
    session["admin_email"] = email
    session["is_admin"] = True
    session["expiry"] = (datetime.now() + timedelta(hours=24)).timestamp()

    return JSONResponse(status_code=200, content={"status": "success", "message": "Admin login successful"})

@router.post("/admin_signup", name="admin_signup")
async def admin_signup(request: Request):
    data = await request.json()
    email = data.get("email")
    password = data.get("password")
    confirm_password = data.get("confirm_password")
    if not email or not password or not confirm_password:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Email and password are required"})
    user = UserModel.get_by_email(email)
    if user:
        return JSONResponse(status_code=400, content={"status": "error", "message": "User already exists"})
    if password != confirm_password:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Passwords do not match"})
    UserModel.create_admin(email, password)
    return JSONResponse(status_code=200, content={"status": "success", "message": "Admin signup successful"})


@router.post("/update_tokens", name="update_tokens")
async def update_tokens(request: Request):
    try:    
        data = await request.json()
        tokens = data.get("tokens")
        if not tokens:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Tokens are required"})
        TokensToConsume.update_token_values(1, tokens)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Tokens updated successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.post("/update_free_tokens", name="update_free_tokens")
async def update_free_tokens(request: Request):
    try:
        data = await request.json()
        tokens = int(data.get("tokens"))
        type = data.get("type")
        if type == "token_value":
            AdminTokenModel.update_token_values(1, tokens)
        elif type == "free_token":
            AdminTokenModel.update_free_tokens(1, tokens)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Tokens updated successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.delete("/delete_file", name="delete_file")
async def delete_file(request: Request):
    try:
        data = await request.json()
        file_id = int(data.get("file_id"))
        knowledge_base_id = int(data.get("knowledge_base_id"))
        file = KnowledgeBaseFileModel.get_by_id(file_id)
        if file:
            if file.knowledge_base_id == knowledge_base_id:
                # Get all files for this knowledge base
                files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base_id)
                
                # Delete the requested file
                KnowledgeBaseFileModel.delete(file_id)
                
                # If this was the last file, delete the knowledge base too
                if len(files) == 1:  # Only had 1 file which we just deleted
                    KnowledgeBaseModel.delete(knowledge_base_id)
                    return JSONResponse(status_code=200, content={
                        "status": "success", 
                        "message": "File and knowledge base deleted successfully"
                    })
                
                return JSONResponse(status_code=200, content={
                    "status": "success", 
                    "message": "File deleted successfully"
                })
            else:
                return JSONResponse(status_code=400, content={
                    "status": "error", 
                    "message": "File not found"
                })
        else:
            return JSONResponse(status_code=400, content={
                "status": "error", 
                "message": "File not found"
            })
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "status": "error", 
            "message": "Something went wrong!", 
            "error": str(e)
        })

@router.post("/update_knowledge_base", name="update_knowledge_base")
async def update_knowledge_base(request: Request):
    try:
        data = await request.json()
        knowledge_base_id = data.get("knowledge_base_id")
        new_name = data.get("new_name")
        KnowledgeBaseModel.update_name(knowledge_base_id, new_name)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Knowledge base name updated successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.post("/upload_file", name="upload_file")
async def upload_file(request: Request):
    try:
        data = await request.form()
        file = data.get("file")
        knowledge_base_id = data.get("knowledge_base_id")
        file_ext = os.path.splitext(file.filename)[1].lower()
        allowed_extensions = {".pdf", ".docx", ".txt"}

        if file_ext not in allowed_extensions:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Unsupported file type"})
        
        # Check if knowledge base already has 5 files
        existing_files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base_id)
        if len(existing_files) >= 5:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Maximum limit of 5 files reached for this knowledge base."}
            )
        # Save file temporarily
        temp_file_path = f"knowledge_base_files/{uuid.uuid4()}_{file.filename}"
        file_path = os.path.join(MEDIA_DIR, temp_file_path)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Extract text
        text_content = extract_text_from_file(file_path)
        if not text_content.strip():
            os.remove(file_path)
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"No readable text found in {file.filename}."}
            )
        KnowledgeBaseFileModel.create(
            knowledge_base_id=knowledge_base_id,
            file_path=temp_file_path,
            file_name=file.filename,
            text_content=text_content
        )
        return JSONResponse(status_code=200, content={"status": "success", "message": "File uploaded successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})





@router.post("/agent_prompt_suggestion", name="agent_prompt_suggestion")
async def agent_prompt_suggestion(request: Request):
    try:
        data = await request.json()
        agent_function = data.get("agent_function")
        agent_tone = data.get("agent_tone")
        level_of_detail = data.get("level_of_detail")
        industry = data.get("industry")
        agent_name = data.get("agent_name", "")

        prompt = generate_agent_prompt(agent_function, agent_tone, level_of_detail, industry, agent_name)
        if prompt:
            return JSONResponse(status_code=200, content={"status": "success", "message": "Prompt generated successfully", "prompt": prompt})
        else:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Some error occured while generating prompt, please try again later!"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.post("/save-agent-prompt", name="save-agent-prompt")
async def save_agent_prompt(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        agent_prompt = data.get("agent_prompt")

        # Create Jinja2 environment
        env = Environment()

        try:
            # Parse the template
            parsed_template = env.parse(agent_prompt)
            # Get all variables used in the template
            new_variables = meta.find_undeclared_variables(parsed_template)
        except Exception as parse_error:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid template syntax! and Use {{_}} to add variables.", "error": str(parse_error)})

        # Get existing dynamic variables if any
        agent = AgentModel.get_by_id(agent_id) if agent_id else None
        existing_variables = agent.dynamic_variable if agent and hasattr(agent, 'dynamic_variable') else {}

        # Merge existing and new variables
        merged_variables = {**existing_variables, **{var: "" for var in new_variables if var not in existing_variables}}
        
        # Save dynamic variables to agent model
        if agent_id and merged_variables:
            AgentModel.update_dynamic_variables(agent_id, merged_variables)
        
        if agent_id:
            if agent:
                AgentModel.update_prompt(agent_id, agent_prompt)
                return JSONResponse(status_code=200, content={"status": "success", "message": "Prompt saved successfully", "dynamic_variables": merged_variables})
            else:
                return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})



@router.post("/save-welcome-message", name="save-welcome-message")
async def save_agent_welcome_message(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        welcome_message = data.get("welcome_msg")
        if agent_id:
            agent = AgentModel.get_by_id(agent_id)
            if agent:
                AgentModel.update_welcome_message(agent_id, welcome_message)
                return JSONResponse(status_code=200, content={"status": "success", "message": "Welcome message saved successfully"})
            else:
                return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/update-agent", name="update-agent")
async def update_agent(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        selected_voice = data.get("selected_voice")
        agent_name = data.get("agent_name")
        if agent_id:
            agent = AgentModel.get_by_id(agent_id)
            if agent:
                if agent_name:
                    AgentModel.update_name(agent_id, agent_name)
                if selected_voice:
                    AgentModel.update_voice(agent_id, selected_voice)
                return JSONResponse(status_code=200, content={"status": "success", "message": "Agent updated successfully"})
            else:
                return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/attach-knowledge-base", name="attach-knowledge-base")
async def attach_knowledge_base(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        knowledge_base_id = data.get("knowledge_base_id")
        if agent_id:
            agent = AgentModel.get_by_id(agent_id)
            if agent:
                from sqlalchemy.orm import sessionmaker
                from app.databases.models import engine
                from sqlalchemy import insert, select, delete
                from app.databases.models import agent_knowledge_association
                Session = sessionmaker(bind=engine)
                session = Session() 
            
                # Check if the association already exists
                query = select(agent_knowledge_association).where(
                    agent_knowledge_association.c.agent_id == agent_id
                )
                result = session.execute(query)
                existing_association = result.fetchone()

                # If the agent has a different knowledge base, delete the old one
                if existing_association and existing_association.knowledge_base_id != knowledge_base_id:
                    delete_stmt = delete(agent_knowledge_association).where(
                        agent_knowledge_association.c.agent_id == agent_id
                    )
                    session.execute(delete_stmt)
                    session.commit()  # Ensure deletion is applied

                if knowledge_base_id:
                    # If no association exists, insert a new one
                    if not existing_association or existing_association.knowledge_base_id != knowledge_base_id:
                        stmt = insert(agent_knowledge_association).values(
                            agent_id=agent_id, 
                            knowledge_base_id=knowledge_base_id
                        )
                        session.execute(stmt)
                        session.commit()

                return JSONResponse(
                    status_code=200,
                    content={"status": "success", "message": "Agent updated successfully", "status_code": 200}
                )
            else:
                return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/toggle-design", name="toggle-design")
async def toggle_design(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        is_enabled = data.get("is_enabled")
        if agent_id:
            agent = AgentModel.get_by_id(agent_id)
            if agent:
                AgentModel.update_design(agent_id, is_enabled)
                return JSONResponse(status_code=200, content={"status": "success", "message": "Design toggle updated successfully"})
            else:
                return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.post("/save-webhook", name="save-webhook")
async def save_webhook(request: Request):
    try:
        data = await request.json()
        webhook_url = data.get("webhook_url")
        user = request.session.get("user")
        user = UserModel.get_by_email(user.get("email"))
        if not user:
            error_response = {
                "status": "error", 
                "error": "User not found",
                "status_code": 400
            }
            return JSONResponse(
                status_code=400,
                content=error_response)
        if WebhookModel.check_webhook_exists(webhook_url, user.id):
            return JSONResponse(status_code=400, content={"status": "error", "message": "Webhook URL already exists"})
        if webhook_url:
            webhook = WebhookModel.create(webhook_url, user.id)
            return JSONResponse(status_code=200, content={"status": "success", "message": "Webhook saved successfully", "webhook_id": webhook.id})
        else:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Webhook URL is required"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.post("/update-webhook", name="update-webhook")
async def update_webhook(request: Request):
    try:
        data = await request.json()
        webhook_id = data.get("webhook_id")
        webhook_url = data.get("webhook_url")
        if webhook_id:
            webhook = WebhookModel.get_by_id(webhook_id)
            if webhook:
                WebhookModel.update_webhook_url(webhook_id, webhook_url)
                return JSONResponse(status_code=200, content={"status": "success", "message": "Webhook updated successfully"})
            else:
                return JSONResponse(status_code=400, content={"status": "error", "message": "Webhook not found"})
        else:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Webhook ID is required"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/custom-functions", name="custom-functions")
async def custom_functions(request: Request):
    try:
        data = await request.json()
        
        function_name = data.get("function_name")
        function_description = data.get("function_description")
        function_url = data.get("function_url")
        function_timeout = data.get("function_timeout")
        function_parameters = data.get("function_parameters", {})
        function_timeout = data.get('function_timeout')
        if not function_timeout:
            function_timeout = None  # or set a default integer like 0

        # Ensure function_parameters is a valid JSON string
        if isinstance(function_parameters, str):
            function_parameters = (
                json.loads(function_parameters) if isinstance(function_parameters, str) and function_parameters.strip() else function_parameters or {}
            )

        if function_url and not is_valid_url(function_url):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Invalid function URL. It must start with http:// or https:// and be a valid URL."
                }
            )

        agent_id = data.get("agent_id")

        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})

        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})

        if not re.match(r'^[A-Za-z_][A-Za-z0-9_.-]{0,63}$', function_name):
            return JSONResponse(
                status_code=400, 
                content={"status": "error", "message": "Invalid function name. Must start with a letter or underscore and contain only letters, digits, underscores (_), dots (.), or dashes (-), max length 64."}
            )

        existing_function = (
            db.session.query(CustomFunctionModel)
            .filter(
                CustomFunctionModel.agent_id == agent_id,
                CustomFunctionModel.function_name == function_name,
            )
            .first()
        )

        if existing_function:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"A function with the name '{function_name}' already exists for this agent."
                }
            )
            

        # Ensure correct parameter order when calling create()
        obj = CustomFunctionModel.create(
            agent_id=agent_id, 
            function_name=function_name, 
            function_description=function_description, 
            function_url=function_url, 
            function_timeout=function_timeout, 
            function_parameters=function_parameters
        )
        response_data = {
            "id": obj.id,
            "function_name": obj.function_name,
            "function_description": obj.function_description,
            "function_url": obj.function_url,
            "function_timeout": obj.function_timeout,
            "function_parameters": obj.function_parameters
        }
        return JSONResponse(status_code=200, content={"status": "success", "message": "Custom function saved successfully", "data": response_data})

    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.put("/edit-custom-functions/{function_id}", name="edit-custom-function")
async def edit_custom_function(function_id: int, request: Request):
    try:
        data = await request.json()

        function_name = data.get("function_name")
        function_description = data.get("function_description")
        function_url = data.get("function_url")
        function_timeout = data.get("function_timeout")
        function_parameters = data.get("function_parameters", {})
        agent_id = data.get("agent_id")

        if not function_timeout:
            function_timeout = None

        if isinstance(function_parameters, str):
            try:
                function_parameters = json.loads(function_parameters.strip() or '{}')
            except json.JSONDecodeError:
                function_parameters = {}
        else:
            function_parameters = function_parameters or {}


        if function_url and not is_valid_url(function_url):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Invalid function URL. It must start with http:// or https:// and be a valid URL."
                }
            )

        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})

        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})

        if not re.match(r'^[A-Za-z_][A-Za-z0-9_.-]{0,63}$', function_name):
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Invalid function name. Must start with a letter or underscore and contain only letters, digits, underscores (_), dots (.), or dashes (-), max length 64."}
            )

        existing_function = (
            db.session.query(CustomFunctionModel)
            .filter(
                CustomFunctionModel.agent_id == agent_id,
                CustomFunctionModel.function_name == function_name,
                CustomFunctionModel.id != function_id 
            )
            .first()
        )

        if existing_function:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"A function with the name '{function_name}' already exists for this agent."
                }
            )

        obj = db.session.query(CustomFunctionModel).filter(CustomFunctionModel.id == function_id).first()
        if not obj:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Custom function not found"})

        obj.function_name = function_name
        obj.function_description = function_description
        obj.function_url = function_url
        obj.function_timeout = function_timeout
        obj.function_parameters = function_parameters

        db.session.commit()
        db.session.refresh(obj)

        response_data = {
            "id": obj.id,
            "function_name": obj.function_name,
            "function_description": obj.function_description,
            "function_url": obj.function_url,
            "function_timeout": obj.function_timeout,
            "function_parameters": obj.function_parameters
        }

        return JSONResponse(status_code=200, content={"status": "success", "message": "Custom function updated successfully", "data": response_data})

    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})
    
@router.get("/get-custom-functions", name="get-custom-functions")
async def get_custom_functions(request: Request):
    try:
        function_id =request.query_params.get('function_id')
        function = CustomFunctionModel.get_by_id(function_id)
        if function:
            function_data = {
                "id": function.id,
                "function_name": function.function_name,
                "function_description": function.function_description,
                "function_url": function.function_url,
                "function_timeout": function.function_timeout,
                "function_parameters": function.function_parameters
            }
            response = {
                "status": "success",
                "message": "Custom functions fetched successfully",
                "data": function_data
            }
            return JSONResponse(status_code=200, content=response)
        else:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Custom function not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.delete("/delete-custom-functions", name="delete-custom-functions")
async def delete_custom_functions(request: Request):
    try:
        data = await request.json()
        function_id = data.get("function_id")
        CustomFunctionModel.delete(function_id)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Custom function deleted successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/save-variables", name="save-variables")
async def save_variables(request: Request):
    try:
        data = await request.json()
        variables = data.get("variables", {})
        agent_id = data.get("agent_id")
        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})
        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})
        
        # Update dynamic variables in the agent model
        AgentModel.update_dynamic_variables(agent_id, variables)
        
        return JSONResponse(status_code=200, content={"status": "success", "message": "Variables saved successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.delete("/remove-variable", name="remove-variable")
async def remove_variable(request: Request):
    try:
        data = await request.json()
        variable_id = data.get("variable_id")
        agent_id = data.get("agent_id")
        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})
        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})
        dynamic_variables = agent.dynamic_variable
        if variable_id in dynamic_variables:
            del dynamic_variables[variable_id] 
        AgentModel.update_dynamic_variables(agent_id, dynamic_variables)
        
        return JSONResponse(status_code=200, content={"status": "success", "message": "Variable removed successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/add-approved-domain", name="add-approved-domain")
async def add_approved_domain(request: Request):
    try:
        data = await request.json()
        domain = data.get("domain")
        user = request.session.get("user")
        user = UserModel.get_by_email(user.get("email"))
        if not user:
            return JSONResponse(status_code=400, content={"status": "error", "message": "User not found"})
        if ApprovedDomainModel.check_domain_exists(domain, user.id):
            return JSONResponse(status_code=400, content={"status": "error", "message": "Domain already exists"})
        domain = ApprovedDomainModel.create(domain, user.id)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Domain added successfully", "domain_id": domain.id})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.delete("/delete-approved-domain", name="delete-approved-domain")
async def delete_approved_domain(request: Request):
    try:
        data = await request.json()
        domain_id = data.get("domain_id")
        if not domain_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Domain ID is required"})
        ApprovedDomainModel.delete(domain_id)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Domain deleted successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/update-agent-settings", name="update-agent-settings")
async def update_agent_settings(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        temperature = data.get("temperature")
        max_output_tokens = data.get("max_output_tokens")   
        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})
        if not temperature:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Temperature is required"})
        if not max_output_tokens:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Max output tokens is required"})
        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})
        AgentModel.update_temperature_and_max_output_tokens(agent_id, temperature, max_output_tokens)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Agent settings updated successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/delete-webhook", name="delete-webhook")
async def delete_webhook(request: Request):
    try:
        data = await request.json()
        webhook_id = data.get("webhook_id")
        if not webhook_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Webhook ID is required"})
        WebhookModel.delete(webhook_id)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Webhook deleted successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/call_details", name="call_details")
async def call_details(request: Request):
    try:
        transcript,summary = None,None
        data = await request.json()
        call_id = data.get("call_id")
        call = AudioRecordings.get_by_id(call_id)
        if call:
            conversation = ConversationModel.get_by_audio_recording_id(call_id)
            if conversation:
                raw_transcript = conversation.transcript
                
                # Format transcript for frontend display
                formatted_transcript = []
                if raw_transcript and isinstance(raw_transcript, list):
                    for msg in raw_transcript:
                        if isinstance(msg, dict):
                            # Handle different transcript formats
                            if 'speaker' in msg and 'text' in msg:
                                # Format: {speaker: "user|agent", text: "message"}
                                formatted_transcript.append({
                                    "role": "assistant" if msg.get("speaker") == "agent" else "user",
                                    "content": msg.get("text", ""),
                                    "timestamp": msg.get("timestamp", ""),
                                    "time_in_call_secs": msg.get("time_in_call_secs", 0)
                                })
                            elif 'role' in msg and 'message' in msg:
                                # Format: {role: "user|assistant", message: "text"}
                                formatted_transcript.append({
                                    "role": msg.get("role", "user"),
                                    "content": msg.get("message", ""),
                                    "timestamp": msg.get("timestamp", ""),
                                    "time_in_call_secs": msg.get("time_in_call_secs", 0)
                                })
                            elif 'role' in msg and 'text' in msg:
                                # Format: {role: "user|assistant", text: "message"}
                                formatted_transcript.append({
                                    "role": msg.get("role", "user"),
                                    "content": msg.get("text", ""),
                                    "timestamp": msg.get("timestamp", ""),
                                    "time_in_call_secs": msg.get("time_in_call_secs", 0)
                                })
                            else:
                                # Fallback: try to extract any text content
                                text_content = msg.get("content") or msg.get("message") or msg.get("text") or str(msg)
                                if text_content and text_content.strip():
                                    formatted_transcript.append({
                                        "role": "user",
                                        "content": text_content,
                                        "timestamp": msg.get("timestamp", ""),
                                        "time_in_call_secs": msg.get("time_in_call_secs", 0)
                                    })
                
                transcript = formatted_transcript
                
                if conversation.summary:
                    summary = conversation.summary
                else:
                    summary = generate_summary(transcript)
                    ConversationModel.update_summary(conversation.id, summary)
            agent = AgentModel.get_by_id(call.agent_id)
            call_details = CallModel.get_by_call_id(call.call_id)
            
            # Get dynamic variables and filter out sensitive/internal fields
            dynamic_variables = call_details.variables if call_details else agent.dynamic_variable
            if dynamic_variables and isinstance(dynamic_variables, dict):
                # Remove only the most sensitive fields that shouldn't be shown to users
                filtered_variables = {k: v for k, v in dynamic_variables.items() 
                                    if k not in ['client_ip', 'agent_id', 'platform', 'elevenlabs_agent_id', 
                                               'query_user_id', 'user_id', 'elevenlabs_user_id']}
            else:
                filtered_variables = {}

            return JSONResponse(status_code=200, content={
                "status": "success", 
                "message": "Call details fetched successfully",
                "call": {
                    "id": call.id,
                    "audio_file": call.audio_file,
                    "created_at": str(call.created_at) if hasattr(call, 'created_at') else None,
                },
                "transcript": transcript,
                "summary": summary,
                "dynamic_variable": filtered_variables
            })
        else:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Call not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/add_url", name="add_url")
async def add_url(request: Request):

    file_path = None
    try:
        data = await request.json()
        url = data.get("url")
        name = data.get("name")
        
        if not url:
            return JSONResponse(
                status_code=400, content={"status": "error", "message": "No url uploaded."}
            )

        user = request.session.get("user")

        # Check if Knowledge Base already exists
        knowledge_base = KnowledgeBaseModel.get_by_name(name, user.get("user_id"))

        if not knowledge_base:
            knowledge_base = KnowledgeBaseModel.create(created_by_id=user.get("user_id"), knowledge_base_name=name, url=url)
        
        temp_file_name = f"{name}_{uuid.uuid4()}.txt"
        temp_file_path = os.path.join(MEDIA_DIR, "knowledge_base_files", temp_file_name)

        if not os.path.exists(temp_file_path):
            os.makedirs(os.path.dirname(temp_file_path), exist_ok=True)
            with open(temp_file_path, "w") as file:
                file.write("")

        # First scrape and get the file
        file_path = scrape_and_get_file(url, temp_file_path)

        # Wait a short time for scraping to complete and file to be written
        await asyncio.sleep(2)

        # Extract text content
        text_content = ""
        try:
            with open(file_path, "r") as file:
                text_content = file.read()
        except FileNotFoundError:
            # If file not ready yet, wait longer and try again
            await asyncio.sleep(5)
            with open(file_path, "r") as file:
                text_content = file.read()

        # First save the file details to database
        knowledge_base_file = KnowledgeBaseFileModel.create(
            knowledge_base_id=knowledge_base.id,
            file_name=name,
            file_path=file_path,
            text_content=text_content
        )

        # Wait for DB save to complete
        await asyncio.sleep(1)

        # Only proceed with vector storage if file was saved successfully
        if knowledge_base_file:
            content_list = []
            content_list.append({
                "file_path": file_path,
                "text_content": text_content
            })

            # Create vector storage in background
            splits = get_splits(content_list)
            vector_id = str(uuid.uuid4())
            if splits:
                status, vector_path = convert_to_vectorstore(splits, vector_id)
                if status:
                    # Update knowledge base with vector info only after successful conversion
                    KnowledgeBaseModel.update(knowledge_base.id, vector_path=vector_path, vector_id=vector_id)

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Knowledge base and files uploaded successfully.", "file_path": file_path}
        )

    except Exception as e:
        knowledge_base = KnowledgeBaseModel.get_by_name(name, user.get("user_id"))
        if knowledge_base:
            files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
            for file in files:
                KnowledgeBaseFileModel.delete(file.id)
            KnowledgeBaseModel.delete(knowledge_base.id)
        # Cleanup any saved files on error
        if file_path:
            os.remove(file_path)    

        print("Error:", str(e))
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.post("/create_text", name="create_text")
async def create_text(request: Request):
    try:
        data = await request.json()
        title = data.get("title")
        content = data.get("content")
        if not title or not content:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Title and content are required"})
        user = request.session.get("user")
        # Check if Knowledge Base already exists
        knowledge_base = KnowledgeBaseModel.get_by_name(title, user.get("user_id"))

        if not knowledge_base:
            knowledge_base = KnowledgeBaseModel.create(created_by_id=user.get("user_id"), knowledge_base_name=title)

        file_path = os.path.join(MEDIA_DIR, f"knowledge_base_files/{title}_{uuid.uuid4()}.txt")

        with open(file_path, "w") as file:
            file.write(content)

        # Save file details to database
        KnowledgeBaseFileModel.create(
            knowledge_base_id=knowledge_base.id,
            file_name=title,
            file_path=file_path,
            text_content=content
        )
        content_list = []
        content_list.append({
                "file_path": file_path,
                "text_content": content
            })

        splits = get_splits(content_list)
        vector_id = str(uuid.uuid4())
        if splits:
            status, vector_path =convert_to_vectorstore(splits, vector_id)
            KnowledgeBaseModel.update(knowledge_base.id, vector_path=vector_path, vector_id=vector_id)
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Knowledge base and files uploaded successfully.", "file_path": file_path}
        )

    except Exception as e:
        knowledge_base = KnowledgeBaseModel.get_by_name(title, user.get("user_id"))
        if knowledge_base:
            files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
            for file in files:
                KnowledgeBaseFileModel.delete(file.id)
            KnowledgeBaseModel.delete(knowledge_base.id)
        # Cleanup any saved files on error
        if file_path:
            os.remove(file_path)    

        print("Error:", str(e))
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/update-agent-token-settings", name="update-agent-token-settings")
async def update_agent_token_settings(request: Request):
    try:
        data = await request.json()
        overall_token_limit = data.get("overall_token_limit")
        daily_token_limit = data.get("daily_token_limit")
        per_call_token_limit = data.get("per_call_token_limit")
        agent_id = data.get("agent_id")
        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})
        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})
        AgentModel.update_value_per_call_token_limit(agent_id, per_call_token_limit)
        if DailyCallLimitModel.get_by_agent_id(agent_id):
            DailyCallLimitModel.update_set_value(agent_id, daily_token_limit)
        else:
            DailyCallLimitModel.create(agent_id, daily_token_limit)
        if OverallTokenLimitModel.get_by_agent_id(agent_id):
            OverallTokenLimitModel.update_set_value(agent_id, overall_token_limit)
        else:
            OverallTokenLimitModel.create(agent_id, overall_token_limit)
        return JSONResponse(status_code=200, content={"status": "success", "message": "Token settings updated successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})



@router.post("/check-payload", name="check-payload")
async def check_payload(request: Request):
    try:
        data = await request.json()
        print(data, "-------------check payload-----------")    
        return JSONResponse(status_code=200, content={"status": "success", "message": "Custom functions fetched successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.post("/toggle-webhook", name="toggle-webhook")
async def toggle_webhook(request: Request):
    try:
        data = await request.json()
        print(data, "-------------toggle-webhook-----------")    
        return JSONResponse(status_code=200, content={"status": "success", "message": "Custom functions fetched successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.post("/save-noise-variables", name="save-noise-variables")
async def save_noise_variables(request: Request):
    try:
        data = await request.json()
        req = SaveNoiseVariablesRequest(**data)
        agent = AgentModel.get_by_id(req.agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})
        
        updated_agent = AgentModel.update_noise_settings(agent_id=req.agent_id, noise_settings=req.variables)

        return JSONResponse(status_code=200, content={"status": "success", "message": "Variables saved successfully"})
    
    except ValidationError as ve:
        if ve.errors():
            first_error = ve.errors()[0]
            ctx_error = first_error.get("ctx", {}).get("error")
            if ctx_error and isinstance(ctx_error, ValueError):
                inner_errors = ctx_error.args[0] if ctx_error.args else {}
                if isinstance(inner_errors, dict) and inner_errors:
                    field_name, msg = list(inner_errors.items())[0]
                    human_msg = f"Invalid value for '{field_name}': {msg}"
                else:
                    human_msg = "Invalid input"
            else:
                loc = first_error.get("loc", ["field"])
                field_name = loc[-1] if loc else "field"
                msg = first_error.get("msg", "Invalid value")
                human_msg = f"Invalid value for '{field_name}': {msg}"
        else:
            human_msg = "Invalid input"
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Validation error", "errors": human_msg},
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@router.get("/agents/{agent_id}/noise-variables")
async def get_noise_variables(agent_id: int):
    agent = AgentModel.get_by_id(agent_id)
    if not agent:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Agent not found"})
    
    noise_vars = agent.noise_setting_variable or {}
        
    response_data = {}
    for key, value in noise_vars.items():
        response_data[key] = {
            "value": value,
            "description": NOISE_SETTINGS_DESCRIPTIONS.get(key,f"Description for {key}"), 
            "is_default": True if DEFAULT_VARS.get(key) == value else False 
        }

    return {"status": "success", "data": response_data}


@router.post("/reset-noise-variables", name="reset-noise-variables")
async def reset_noise_variables(request: Request):
    try:
        data = await request.json()
        req = ResetNoiseVariablesRequest.model_validate(data)

        agent_id = req.agent_id
        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Agent not found"}
            )

        var_to_reset = req.variables[-1]
        current_vars = agent.noise_setting_variable or {}

        if var_to_reset in DEFAULT_VARS:
            current_vars[var_to_reset] = DEFAULT_VARS[var_to_reset]

        updated_agent = AgentModel.update_noise_settings(agent_id=agent_id, noise_settings=current_vars)

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Variables reset to default successfully","value":getattr(updated_agent,"noise_setting_variable",{}).get(var_to_reset)}
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Something went wrong!", "error": str(e)}
        )
