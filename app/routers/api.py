from fastapi import APIRouter,Request, Response
from .schemas.format import (
    ErrorResponse, 
    SuccessResponse, 
)
from app.core import logger
from app.services import AudioStorage
from starlette.responses import JSONResponse, RedirectResponse
from app.databases.models import AudioRecordModel, UserModel, AgentModel, ResetPasswordModel, AgentConnectionModel, AudioRecordings
from app.databases.schema import  AudioRecordListSchema
import json
import bcrypt
from app.utils.helper import make_outbound_call, generate_twiml
from fastapi_mail import FastMail, MessageSchema,ConnectionConfig
import os
import uuid
from pydantic import BaseModel
from datetime import datetime
import shutil
import os
from app.databases.models import KnowledgeBaseModel
from app.utils.helper import extract_text_from_file
from config import MEDIA_DIR  # ✅ Import properly

router = APIRouter(prefix="/api")


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
@router.post("/user-login/",
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

@router.post("/user-register/",
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
        # Create user using SQLAlchemy
        user = UserModel.create(email=email, name=name, password=password, is_verified=False)
        if not ResetPasswordModel.get_by_email(email):    
            ResetPasswordModel.create(email=email, token=email_token)
        else:
            ResetPasswordModel.update(email=email, token=email_token)

        template = f"""
                    <html>
                    <body>                    

                    <p>Hi {user.name} !!!
                            <br>Please click on the link below to verify your account
                            <br>
                            <a href="http://localhost:8000/verify-account/{email_token}">Verify Account</a>
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

@router.delete("/delete_agent/",
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
        xml = generate_twiml(agent,url)
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
    temp_file_path = None
    try:
        data = await request.form()
        # Validate file extension
        attachment = data.get("attachment")
        name = data.get("name")
        allowed_extensions = {".pdf", ".docx", ".txt"}
        file_ext = os.path.splitext(attachment.filename)[1].lower()
        if file_ext not in allowed_extensions:
            return JSONResponse(
                status_code=400, content={"status": "error", "message": "Unsupported file type."}
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
                content={"status": "error", "message": "No readable text found in the uploaded file."},
            )
        user = request.session.get("user")
        # Save to database
        new_knowledge_base = KnowledgeBaseModel.create(
            created_by_id=user.get("user_id"),
            knowledge_base_name=name,
            attachment_path=temp_file_path,
            text_content=text_content,
            attachment_name=attachment.filename
        )
   
        return JSONResponse(
            status_code=200, content={"status": "success", "message": "Knowledge base uploaded successfully"}
        )

    except Exception as e:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)  # Ensure cleanup on error
        print("Error", str(e))
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@router.delete("/delete_knowledge_base", name="delete_knowledge_base")
async def delete_knowledge_base(request: Request):
    data = await request.json()
    knowledge_base_id = data.get("knowledge_base_id")
    KnowledgeBaseModel.delete(knowledge_base_id)
    return JSONResponse(status_code=200, content={"status": "success", "message": "Knowledge base deleted successfully"})



@router.post("/save_changes", name="save_changes")
async def save_changes(request: Request):
    # try:
    # Validate request has valid JSON body
    data = await request.json()
    print(data)
    # Validate required fields
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
    # except Exception as e:
    #     return JSONResponse(
    #         status_code=500,
    #         content={"status": "error", "message": "Something went wrong!", "error": str(e)}
    #     )

@router.get("/get_agent_connection", name="get_agent_connection")
async def get_agent_connection(request: Request, agent_id: str):
    try:
        connection = AgentConnectionModel.get_by_agent_id(agent_id)
        if connection:
            connection_data = {
                "icon_url": connection.icon_url,
                "primary_color": connection.primary_color,
                "secondary_color": connection.secondary_color,
                "pulse_color": connection.pulse_color
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
