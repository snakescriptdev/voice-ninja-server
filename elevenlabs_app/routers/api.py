from fastapi import APIRouter,Request, Response
from app.core import logger
from app.services import AudioStorage
from starlette.responses import JSONResponse, RedirectResponse
from app.databases.models import (
    AudioRecordModel, ElevenLabModel, LLMModel, UserModel,
    AgentModel, ResetPasswordModel, 
    AgentConnectionModel, PaymentModel, 
    AdminTokenModel, AudioRecordings, 
    TokensToConsume, ApprovedDomainModel, CallModel,
    KnowledgeBaseModel, KnowledgeBaseFileModel, WebhookModel, 
    CustomFunctionModel,ConversationModel, DailyCallLimitModel,
    OverallTokenLimitModel, VoiceModel
    )
import json
from elevenlabs_app.services.eleven_lab_agent_utils import ElevenLabsAgentCRUD
from jinja2 import Environment, meta
from fastapi_sqlalchemy import db
from elevenlabs_app.elevenlabs_config import DEFAULT_LANGUAGE,DEFAULT_LLM_ELEVENLAB,DEFAULT_MODEL_ELEVENLAB,ELEVENLABS_MODELS,VALID_LLMS

from sqlalchemy.orm import sessionmaker
from app.databases.models import engine
from sqlalchemy import insert, select,delete
from app.databases.models import agent_knowledge_association

ElevenLabsAPIRouter = APIRouter()

@ElevenLabsAPIRouter.post("/create_new_agent",name='create-new-agent')
async def create_new_agent(request: Request):
    try:
        data = await request.json()
        user_id = request.session.get("user").get("user_id")
        agent_name = data.get("agent_name")
        agent_prompt = data.get("agent_prompt")
        welcome_msg = data.get("welcome_msg")
        selected_model = data.get("selected_model")#seleted llm model by user 
        selected_voice = data.get("selected_voice")
        selected_language = data.get("selected_language")
        phone_number = data.get("phone_number", '+17752648387')
        selected_knowledge_base = data.get("selected_knowledge_base")

        elevenlabs_voice_id = VoiceModel.get_by_id(selected_voice).elevenlabs_voice_id
        selected_llm_model_rec = LLMModel.get_by_id(selected_model)

        selected_model_rec = ElevenLabModel.get_by_name(DEFAULT_MODEL_ELEVENLAB)
        language_in_selected_model = [x for x in selected_model_rec.languages if x['code']==selected_language]
        if not language_in_selected_model:
            error_response = {
                "status": "error", 
                "error": f"Selected Language not aloowed.",
                "status_code": 500
            }   
            return JSONResponse(
                status_code=500,
                content=error_response
            )

        try:
            with db():
                agent = AgentModel(
                    created_by=user_id,
                    agent_name=agent_name,
                    agent_prompt=agent_prompt,
                    selected_llm_model = selected_model,
                    selected_model_id = selected_model_rec.id,
                    welcome_msg=welcome_msg,
                    selected_voice=selected_voice,
                    selected_language=selected_language,
                    phone_number=phone_number
                )
                db.session.add(agent)
                db.session.flush()

                agent_connection = AgentConnectionModel(agent_id=agent.id)
                db.session.add(agent_connection)

                creator = ElevenLabsAgentCRUD()
                api_response = creator.create_agent(
                    name=agent_name,
                    prompt=agent_prompt,
                    model=selected_llm_model_rec.name,
                    voice_id=elevenlabs_voice_id,
                    language=selected_language,
                    selected_elevenlab_model = DEFAULT_MODEL_ELEVENLAB,
                    first_message = welcome_msg
                )

                if not api_response or "agent_id" not in api_response:
                    raise Exception("Failed to create ElevenLabs agent")

                agent.elvn_lab_agent_id = api_response["agent_id"]
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            error_response = {
                "status": "error", 
                "error": f"Error creating agent: {str(e)}",
                "status_code": 500
            }   
            return JSONResponse(
                status_code=500,
                content=error_response
            )

        if selected_knowledge_base:
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

# @ElevenLabsAPIRouter.post("/edit_agent",name='edit-agent')
# async def edit_agent(request: Request):
#     try:
#         data = await request.json()
#         agent_id = data.get("agent_id")
#         agent_name = data.get("agent_name")
#         agent_prompt = data.get("agent_prompt")
#         welcome_msg = data.get("welcome_msg")
#         selected_model = data.get("selected_model")
#         selected_voice = data.get("selected_voice")
#         selected_language = data.get("selected_language")
#         phone_number = data.get("phone_number", '+17752648387')
#         selected_knowledge_base = data.get("selected_knowledge_base")

#         if not agent_id:
#             return JSONResponse(
#                 status_code=400,
#                 content={"status": "error", "message": "Missing agent_id", "status_code": 400}
#             )
#         Session = sessionmaker(bind=engine)
#         session = Session() 
#         # Update Agent Details
#         session.execute(
#             AgentModel.__table__.update()
#             .where(AgentModel.id == agent_id)
#             .values(
#                 agent_name=agent_name,
#                 agent_prompt=agent_prompt,
#                 welcome_msg=welcome_msg,
#                 selected_model=selected_model,
#                 selected_voice=selected_voice,
#                 selected_language=selected_language,
#                 phone_number=phone_number,
#             )
#         )
#         session.commit()

#         try:
#             # Check if the association already exists
#             query = select(agent_knowledge_association).where(
#                 agent_knowledge_association.c.agent_id == agent_id
#             )
#             result = session.execute(query)
#             existing_association = result.fetchone()

#             # If the agent has a different knowledge base, delete the old one
#             if existing_association and existing_association.knowledge_base_id != selected_knowledge_base:
#                 delete_stmt = delete(agent_knowledge_association).where(
#                     agent_knowledge_association.c.agent_id == agent_id
#                 )
#                 session.execute(delete_stmt)
#                 session.commit()  # Ensure deletion is applied

#             if selected_knowledge_base:
#                 # If no association exists, insert a new one
#                 if not existing_association or existing_association.knowledge_base_id != selected_knowledge_base:
#                     stmt = insert(agent_knowledge_association).values(
#                         agent_id=agent_id, 
#                         knowledge_base_id=selected_knowledge_base
#                     )
#                     session.execute(stmt)
#                     session.commit()

#             return JSONResponse(
#                 status_code=200,
#                 content={"status": "success", "message": "Agent updated successfully", "status_code": 200}
#             )

#         except Exception as e:
#             session.rollback()
#             return JSONResponse(
#                 status_code=500,
#                 content={"status": "error", "message": f"Error updating agent: {str(e)}", "status_code": 500}
#             )

#     except Exception as e:
#         return JSONResponse(
#             status_code=500,
#             content={"status": "error", "message": f"Error updating agent: {str(e)}", "status_code": 500}
#         )

@ElevenLabsAPIRouter.post("/edit_agent",name='edit-agent')
async def edit_agent(request: Request):
    try:
        data = await request.json()
        user_id = request.session.get("user").get("user_id")
        agent_id = data.get("agent_id")
        agent_name = data.get("agent_name")

        phone_number = data.get("phone_number", '+17752648387')
        selected_knowledge_base = data.get("selected_knowledge_base")

        selected_llm_model_id = data.get("selected_llm_model_id")
        selected_voice_id = data.get("selected_voice_id")
        selected_language_code = data.get("selected_language_code")
        welcome_msg = data.get("welcome_msg")
        prompt = data.get("prompt")

        if not agent_id:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Missing agent_id", "status_code": 400}
            )

        agent_rec = AgentModel.get_by_id(agent_id)
        if not agent_rec:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": "Agent not found"}
            )

        if agent_rec.created_by!=user_id:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": "Agent not owned by you."}
            )

        elevenlabs_voice_id = VoiceModel.get_by_id(selected_voice_id).elevenlabs_voice_id
        selected_llm_model_rec = LLMModel.get_by_id(selected_llm_model_id)

        selected_model_rec = ElevenLabModel.get_by_name(DEFAULT_MODEL_ELEVENLAB)
        language_in_selected_model = [x for x in selected_model_rec.languages if x['code']==selected_language_code]
        if not language_in_selected_model:
            error_response = {
                "status": "error", 
                "error": f"Selected Language not aloowed.",
                "status_code": 500
            }   
            return JSONResponse(
                status_code=500,
                content=error_response
            )

        try:
            with db():
                agent = AgentModel(
                    created_by=user_id,
                    agent_name=agent_name,
                    agent_prompt=agent_prompt,
                    selected_llm_model = selected_model,
                    selected_model_id = selected_model_rec.id,
                    welcome_msg=welcome_msg,
                    selected_voice=selected_voice,
                    selected_language=selected_language,
                    phone_number=phone_number
                )
                db.session.add(agent)
                db.session.flush()

                agent_connection = AgentConnectionModel(agent_id=agent.id)
                db.session.add(agent_connection)

                creator = ElevenLabsAgentCRUD()
                api_response = creator.create_agent(
                    name=agent_name,
                    prompt=agent_prompt,
                    model=selected_llm_model_rec.name,
                    voice_id=elevenlabs_voice_id,
                    language=selected_language,
                    selected_elevenlab_model = DEFAULT_MODEL_ELEVENLAB,
                    first_message = welcome_msg
                )

                if not api_response or "agent_id" not in api_response:
                    raise Exception("Failed to create ElevenLabs agent")

                agent.elvn_lab_agent_id = api_response["agent_id"]
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            error_response = {
                "status": "error", 
                "error": f"Error creating agent: {str(e)}",
                "status_code": 500
            }   
            return JSONResponse(
                status_code=500,
                content=error_response
            )

        Session = sessionmaker(bind=engine)
        session = Session() 
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

# @ElevenLabsAPIRouter.post("/save-agent-prompt", name="save-agent-prompt")
# async def save_agent_prompt(request: Request):
#     try:
#         data = await request.json()
#         agent_id = data.get("agent_id")
#         agent_prompt = data.get("agent_prompt")

#         # Create Jinja2 environment
#         env = Environment()

#         try:
#             # Parse the template
#             parsed_template = env.parse(agent_prompt)
#             # Get all variables used in the template
#             new_variables = meta.find_undeclared_variables(parsed_template)
#         except Exception as parse_error:
#             return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid template syntax! and Use {{_}} to add variables.", "error": str(parse_error)})

#         # Get existing dynamic variables if any
#         agent = AgentModel.get_by_id(agent_id) if agent_id else None
#         existing_variables = agent.dynamic_variable if agent and hasattr(agent, 'dynamic_variable') else {}

#         # Merge existing and new variables
#         merged_variables = {**existing_variables, **{var: "" for var in new_variables if var not in existing_variables}}
        
#         # Save dynamic variables to agent model
#         if agent_id and merged_variables:
#             AgentModel.update_dynamic_variables(agent_id, merged_variables)
        
#         if agent_id:
#             if agent:
#                 AgentModel.update_prompt(agent_id, agent_prompt)
#                 return JSONResponse(status_code=200, content={"status": "success", "message": "Prompt saved successfully", "dynamic_variables": merged_variables})
#             else:
#                 return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
#         else:
#             return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
#     except Exception as e:
#         return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

# @ElevenLabsAPIRouter.post("/custom-functions", name="custom-functions")
# async def custom_functions(request: Request):
#     try:
#         data = await request.json()
        
#         function_name = data.get("function_name")
#         function_description = data.get("function_description")
#         function_url = data.get("function_url")
#         function_timeout = data.get("function_timeout")
#         function_parameters = data.get("function_parameters", {})
#         function_timeout = data.get('function_timeout')
#         if not function_timeout:
#             function_timeout = None  # or set a default integer like 0

#         # Ensure function_parameters is a valid JSON string
#         if isinstance(function_parameters, str):
#             function_parameters = (
#                 json.loads(function_parameters) if isinstance(function_parameters, str) and function_parameters.strip() else function_parameters or {}
#             )

#         if function_url and not is_valid_url(function_url):
#             return JSONResponse(
#                 status_code=400,
#                 content={
#                     "status": "error",
#                     "message": "Invalid function URL. It must start with http:// or https:// and be a valid URL."
#                 }
#             )

#         agent_id = data.get("agent_id")

#         if not agent_id:
#             return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})

#         agent = AgentModel.get_by_id(agent_id)
#         if not agent:
#             return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})

#         if not re.match(r'^[A-Za-z_][A-Za-z0-9_.-]{0,63}$', function_name):
#             return JSONResponse(
#                 status_code=400, 
#                 content={"status": "error", "message": "Invalid function name. Must start with a letter or underscore and contain only letters, digits, underscores (_), dots (.), or dashes (-), max length 64."}
#             )

#         existing_function = (
#             db.session.query(CustomFunctionModel)
#             .filter(
#                 CustomFunctionModel.agent_id == agent_id,
#                 CustomFunctionModel.function_name == function_name,
#             )
#             .first()
#         )

#         if existing_function:
#             return JSONResponse(
#                 status_code=400,
#                 content={
#                     "status": "error",
#                     "message": f"A function with the name '{function_name}' already exists for this agent."
#                 }
#             )
            

#         # Ensure correct parameter order when calling create()
#         obj = CustomFunctionModel.create(
#             agent_id=agent_id, 
#             function_name=function_name, 
#             function_description=function_description, 
#             function_url=function_url, 
#             function_timeout=function_timeout, 
#             function_parameters=function_parameters
#         )
#         response_data = {
#             "id": obj.id,
#             "function_name": obj.function_name,
#             "function_description": obj.function_description,
#             "function_url": obj.function_url,
#             "function_timeout": obj.function_timeout,
#             "function_parameters": obj.function_parameters
#         }
#         return JSONResponse(status_code=200, content={"status": "success", "message": "Custom function saved successfully", "data": response_data})

#     except Exception as e:
#         return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

# @ElevenLabsAPIRouter.put("/edit-custom-functions/{function_id}", name="edit-custom-function")
# async def edit_custom_function(function_id: int, request: Request):
#     try:
#         data = await request.json()

#         function_name = data.get("function_name")
#         function_description = data.get("function_description")
#         function_url = data.get("function_url")
#         function_timeout = data.get("function_timeout")
#         function_parameters = data.get("function_parameters", {})
#         agent_id = data.get("agent_id")

#         if not function_timeout:
#             function_timeout = None

#         if isinstance(function_parameters, str):
#             try:
#                 function_parameters = json.loads(function_parameters.strip() or '{}')
#             except json.JSONDecodeError:
#                 function_parameters = {}
#         else:
#             function_parameters = function_parameters or {}


#         if function_url and not is_valid_url(function_url):
#             return JSONResponse(
#                 status_code=400,
#                 content={
#                     "status": "error",
#                     "message": "Invalid function URL. It must start with http:// or https:// and be a valid URL."
#                 }
#             )

#         if not agent_id:
#             return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})

#         agent = AgentModel.get_by_id(agent_id)
#         if not agent:
#             return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})

#         if not re.match(r'^[A-Za-z_][A-Za-z0-9_.-]{0,63}$', function_name):
#             return JSONResponse(
#                 status_code=400,
#                 content={"status": "error", "message": "Invalid function name. Must start with a letter or underscore and contain only letters, digits, underscores (_), dots (.), or dashes (-), max length 64."}
#             )

#         existing_function = (
#             db.session.query(CustomFunctionModel)
#             .filter(
#                 CustomFunctionModel.agent_id == agent_id,
#                 CustomFunctionModel.function_name == function_name,
#                 CustomFunctionModel.id != function_id 
#             )
#             .first()
#         )

#         if existing_function:
#             return JSONResponse(
#                 status_code=400,
#                 content={
#                     "status": "error",
#                     "message": f"A function with the name '{function_name}' already exists for this agent."
#                 }
#             )

#         obj = db.session.query(CustomFunctionModel).filter(CustomFunctionModel.id == function_id).first()
#         if not obj:
#             return JSONResponse(status_code=404, content={"status": "error", "message": "Custom function not found"})

#         obj.function_name = function_name
#         obj.function_description = function_description
#         obj.function_url = function_url
#         obj.function_timeout = function_timeout
#         obj.function_parameters = function_parameters

#         db.session.commit()
#         db.session.refresh(obj)

#         response_data = {
#             "id": obj.id,
#             "function_name": obj.function_name,
#             "function_description": obj.function_description,
#             "function_url": obj.function_url,
#             "function_timeout": obj.function_timeout,
#             "function_parameters": obj.function_parameters
#         }

#         return JSONResponse(status_code=200, content={"status": "success", "message": "Custom function updated successfully", "data": response_data})

#     except Exception as e:
#         return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})
    
# @ElevenLabsAPIRouter.get("/get-custom-functions", name="get-custom-functions")
# async def get_custom_functions(request: Request):
#     try:
#         function_id =request.query_params.get('function_id')
#         function = CustomFunctionModel.get_by_id(function_id)
#         if function:
#             function_data = {
#                 "id": function.id,
#                 "function_name": function.function_name,
#                 "function_description": function.function_description,
#                 "function_url": function.function_url,
#                 "function_timeout": function.function_timeout,
#                 "function_parameters": function.function_parameters
#             }
#             response = {
#                 "status": "success",
#                 "message": "Custom functions fetched successfully",
#                 "data": function_data
#             }
#             return JSONResponse(status_code=200, content=response)
#         else:
#             return JSONResponse(status_code=400, content={"status": "error", "message": "Custom function not found"})
#     except Exception as e:
#         return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


# @ElevenLabsAPIRouter.delete("/delete-custom-functions", name="delete-custom-functions")
# async def delete_custom_functions(request: Request):
#     try:
#         data = await request.json()
#         function_id = data.get("function_id")
#         CustomFunctionModel.delete(function_id)
#         return JSONResponse(status_code=200, content={"status": "success", "message": "Custom function deleted successfully"})
#     except Exception as e:
#         return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

