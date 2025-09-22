import os
import re
import shutil
import uuid
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter,Request, Response
from app.core import logger
from app.services import AudioStorage
from starlette.responses import JSONResponse, RedirectResponse
from app.databases.models import (
    AudioRecordModel, ElevenLabModel, LLMModel, UserModel,
    AgentModel, ResetPasswordModel, ElevenLabsWebhookToolModel,
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
from sqlalchemy import insert, select, delete, func
from app.databases.models import agent_knowledge_association
from config import MEDIA_DIR  # Import properly
from app.utils.helper import extract_text_from_file, is_valid_url
# from app.utils.langchain_integration import get_splits, convert_to_vectorstore  # Removed - using ElevenLabs knowledge base directly




ElevenLabsAPIRouter = APIRouter()

# Lightweight helper for dashboard: return LLM model names by comma-separated ids
@ElevenLabsAPIRouter.get("/llm_models")
async def get_llm_models(request: Request):
    try:
        ids_str = request.query_params.get("ids", "")
        if not ids_str:
            return {"models": {}}
        id_list = []
        for x in ids_str.split(','):
            try:
                id_list.append(int(x))
            except Exception:
                continue
        if not id_list:
            return {"models": {}}
        rows = db.session.query(LLMModel.id, LLMModel.name).filter(LLMModel.id.in_(id_list)).all()
        return {"models": {row.id: row.name for row in rows}}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

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

        # Enforce and auto-correct ElevenLabs model selection rules
        ENGLISH_CODES = ["en", "en-US", "en-GB"]
        EN_MODELS = ["eleven_turbo_v2", "eleven_flash_v2"]
        NON_EN_MODELS = ["eleven_turbo_v2_5", "eleven_flash_v2_5"]
        selected_elevenlab_model = data.get("selected_elevenlab_model") or DEFAULT_MODEL_ELEVENLAB
        if selected_language and selected_elevenlab_model:
            if selected_language in ENGLISH_CODES:
                if selected_elevenlab_model not in EN_MODELS:
                    selected_elevenlab_model = "eleven_turbo_v2"  # auto-correct to default English model
            else:
                if selected_elevenlab_model not in NON_EN_MODELS:
                    selected_elevenlab_model = "eleven_turbo_v2_5"  # auto-correct to default non-English model

        selected_model_rec = ElevenLabModel.get_by_name(selected_elevenlab_model)
        language_in_selected_model = [x for x in selected_model_rec.languages if x['code']==selected_language]
        if not language_in_selected_model:
            error_response = {
                "status": "error", 
                "error": f"Selected Language not allowed.",
                "status_code": 500
            }   
            return JSONResponse(
                status_code=500,
                content=error_response
            )

        try:
            # First create agent in ElevenLabs
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

            # Check if ElevenLabs agent creation was successful
            if not api_response or "agent_id" not in api_response or "error" in api_response:
                error_msg = api_response.get("error", "Unknown error") if api_response else "No response from ElevenLabs"
                raise Exception(f"Failed to create ElevenLabs agent: {error_msg}")

            # Only create local agent if ElevenLabs was successful
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
                    phone_number=phone_number,
                    elvn_lab_agent_id=api_response["agent_id"]  # Set ElevenLabs ID immediately
                )
                db.session.add(agent)
                db.session.flush()

                agent_connection = AgentConnectionModel(agent_id=agent.id)
                db.session.add(agent_connection)
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

@ElevenLabsAPIRouter.delete("/delete_agent",name='delete-agent')
@ElevenLabsAPIRouter.delete("/delete_agent/",name='delete-agent-trailing')
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
        elevenlabs_agent_id = agent.elvn_lab_agent_id
        if not agent:
            error_response = {
                "status": "error", 
                "error": "Agent not found",
                "status_code": 400
            }
            return JSONResponse(status_code=400, content=error_response)
        ElevenLabsAgentCRUD().delete_agent(elevenlabs_agent_id)
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

def update_agent_database(agent_rec, update_data, agent_id):
    """
    Update agent in local database with proper error handling
    
    Args:
        agent_rec: AgentModel instance (may not be in current session)
        update_data: Dict containing fields to update
        agent_id: ID of the agent to update
        
    Returns:
        tuple: (success: bool, error_message: str, updated_agent: AgentModel)
    """
    try:
        with db():
            # Get a fresh reference to the agent in the current session
            current_agent = db.session.query(AgentModel).filter(AgentModel.id == agent_id).first()
            if not current_agent:
                return False, f"Agent with ID {agent_id} not found in current session", None
            
            # Update only the fields that are provided
            for field, value in update_data.items():
                if value is not None and hasattr(current_agent, field):
                    setattr(current_agent, field, value)
                    # print(f"🔍 Debug: Updated {field} to: {value}")
            
            # Update the updated_at timestamp
            current_agent.updated_at = func.now()
            
            # print(f"🔍 Debug: About to commit changes to database...")
            db.session.commit()
            # print(f"🔍 Debug: Database commit successful!")
            
            # Refresh the object to ensure we have latest values
            db.session.refresh(current_agent)
            # print(f"🔍 Debug: After refresh - agent_prompt: {current_agent.agent_prompt}")
            # print(f"🔍 Debug: After refresh - selected_language: {current_agent.selected_language}")
            
            # Verify by direct database query
            from sqlalchemy import text
            result = db.session.execute(
                text("SELECT agent_prompt, selected_language, welcome_msg FROM agents WHERE id = :agent_id"), 
                {"agent_id": agent_id}
            )
            row = result.fetchone()
            # print(f"🔍 Debug: Direct DB query - agent_prompt: {row[0] if row else 'None'}, selected_language: {row[1] if row else 'None'}, welcome_msg: {row[2] if row else 'None'}")
            
            return True, None, current_agent
            
    except Exception as e:
        # print(f"🔍 Debug: Database update error: {str(e)}")
        return False, str(e), None


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

        # Get existing agent record
        agent_rec = AgentModel.get_by_id(agent_id)
        if not agent_rec:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": "Agent not found", "status_code": 404}
            )

        if agent_rec.created_by != user_id:
            return JSONResponse(
                status_code=403,
                content={"status": "error", "message": "Agent not owned by you.", "status_code": 403}
            )

        # Validate voice selection
        if selected_voice_id:
            voice_rec = VoiceModel.get_by_id(selected_voice_id)
            if not voice_rec:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Selected voice not found", "status_code": 400}
                )
            elevenlabs_voice_id = voice_rec.elevenlabs_voice_id
        else:
            elevenlabs_voice_id = None

        # Validate LLM model selection
        if selected_llm_model_id:
            selected_llm_model_rec = LLMModel.get_by_id(selected_llm_model_id)
            if not selected_llm_model_rec:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Selected LLM model not found", "status_code": 400}
                )
        else:
            selected_llm_model_rec = None


        # Enforce and auto-correct ElevenLabs model selection rules
        ENGLISH_CODES = ["en", "en-US", "en-GB"]
        EN_MODELS = ["eleven_turbo_v2", "eleven_flash_v2"]
        NON_EN_MODELS = ["eleven_turbo_v2_5", "eleven_flash_v2_5"]
        selected_elevenlab_model = data.get("selected_elevenlab_model") or DEFAULT_MODEL_ELEVENLAB
        if selected_language_code and selected_elevenlab_model:
            if selected_language_code in ENGLISH_CODES:
                if selected_elevenlab_model not in EN_MODELS:
                    selected_elevenlab_model = "eleven_turbo_v2"  # auto-correct to default English model
            else:
                if selected_elevenlab_model not in NON_EN_MODELS:
                    selected_elevenlab_model = "eleven_turbo_v2_5"  # auto-correct to default non-English model

        # Validate language selection
        if selected_language_code:
            selected_model_rec = ElevenLabModel.get_by_name(selected_elevenlab_model)
            if selected_model_rec and hasattr(selected_model_rec, 'languages'):
                language_in_selected_model = [x for x in selected_model_rec.languages if x.get('code') == selected_language_code]
                if not language_in_selected_model:
                    return JSONResponse(
                        status_code=400,
                        content={"status": "error", "message": f"Selected language '{selected_language_code}' not allowed for this model", "status_code": 400}
                    )
            else:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Model language validation failed", "status_code": 400}
                )

        # Prepare update data first
        update_data = {}
        
        # Validate prompt template syntax and extract dynamic variables if prompt is provided
        if prompt:
            try:
                # Create Jinja2 environment
                env = Environment()
                # Parse the template to validate syntax
                parsed_template = env.parse(prompt)
                # Basic validation - if parsing succeeds, syntax is valid
                
                # Extract dynamic variables from the prompt
                from jinja2 import meta
                new_variables = meta.find_undeclared_variables(parsed_template)
                # print(f"🔍 Debug: Dynamic variables found in prompt: {list(new_variables)}")
                
                # Get existing dynamic variables if any
                existing_variables = agent_rec.dynamic_variable if hasattr(agent_rec, 'dynamic_variable') else {}
                # print(f"🔍 Debug: Existing dynamic variables: {existing_variables}")
                
                # Merge existing and new variables
                merged_variables = {**existing_variables, **{var: "" for var in new_variables if var not in existing_variables}}
                # print(f"🔍 Debug: Merged dynamic variables: {merged_variables}")
                
                # Add dynamic variables to update data
                if merged_variables:
                    update_data['dynamic_variable'] = merged_variables
                    # print(f"🔍 Debug: Added dynamic_variable to update data")
                
            except Exception as parse_error:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error", 
                        "message": "Invalid template syntax! Use {{variable_name}} format for variables.", 
                        "error": str(parse_error),
                        "status_code": 400
                    }
                )
        
        # Add other fields to update data
        if agent_name:
            update_data['agent_name'] = agent_name
        if prompt:
            update_data['agent_prompt'] = prompt
        if selected_llm_model_rec:
            update_data['selected_llm_model'] = selected_llm_model_rec.id
            update_data['selected_model_id'] = selected_llm_model_rec.id
        if welcome_msg:
            update_data['welcome_msg'] = welcome_msg
        if selected_voice_id:
            update_data['selected_voice'] = selected_voice_id
        if selected_language_code:
            update_data['selected_language'] = selected_language_code
        if phone_number:
            update_data['phone_number'] = phone_number

        # print(f"🔍 Debug: Fields to update: {list(update_data.keys())}")
        # if 'welcome_msg' in update_data:
            # print(f"🔍 Debug: Welcome message to update: {update_data['welcome_msg']}")
        # if 'dynamic_variable' in update_data:
            # print(f"🔍 Debug: Dynamic variables to update: {update_data['dynamic_variable']}")
        
        # Update local database using the dedicated function
        db_success, db_error, updated_agent = update_agent_database(agent_rec, update_data, agent_id)
        
        if not db_success:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error", 
                    "error": f"Database update failed: {db_error}",
                    "status_code": 500
                }
            )

        # Update ElevenLabs agent if we have an existing ElevenLabs agent ID
        if agent_rec.elvn_lab_agent_id:
            # print(f"🔍 Debug: About to call ElevenLabs update_agent:")
            # print(f"  - agent_id: {agent_rec.elvn_lab_agent_id}")
            # print(f"  - name: {agent_name if agent_name else None}")
            # print(f"  - prompt: {prompt if prompt else None}")
            # print(f"  - model: {selected_llm_model_rec.name if selected_llm_model_rec else None}")
            # print(f"  - voice_id: {elevenlabs_voice_id if elevenlabs_voice_id else None}")
            # print(f"  - language: {selected_language_code if selected_language_code else None}")
            # print(f"  - selected_elevenlab_model: {DEFAULT_MODEL_ELEVENLAB if DEFAULT_MODEL_ELEVENLAB else None}")
            # print(f"  - first_message: {welcome_msg if welcome_msg else None}")
            # if welcome_msg:
            #     print(f"🔍 Debug: ElevenLabs will receive welcome message: {welcome_msg}")
            
            # Get dynamic variables from update data if available
            dynamic_vars = update_data.get('dynamic_variable')
            # if dynamic_vars:
            #     print(f"🔍 Debug: ElevenLabs will receive dynamic variables: {dynamic_vars}")
            
            try:
                creator = ElevenLabsAgentCRUD()
                api_response = creator.update_agent(
                    agent_id=agent_rec.elvn_lab_agent_id,  # Use existing ElevenLabs agent ID
                    name=agent_name if agent_name else None,
                    prompt=prompt if prompt else None,
                    model=selected_llm_model_rec.name if selected_llm_model_rec else None,
                    voice_id=elevenlabs_voice_id if elevenlabs_voice_id else None,
                    language=selected_language_code if selected_language_code else None,
                    selected_elevenlab_model=DEFAULT_MODEL_ELEVENLAB if DEFAULT_MODEL_ELEVENLAB else None,
                    first_message=welcome_msg if welcome_msg else None,
                    dynamic_variables=dynamic_vars
                )

                if api_response and "error" in api_response:
                    # Log the error but don't fail the entire update
                    print(f"ElevenLabs update warning: {api_response.get('error')}")
            except Exception as e:
                # Log ElevenLabs error but don't rollback database changes
                print(f"ElevenLabs update error (but database changes preserved): {str(e)}")

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

            # Save dynamic variables to local database if they exist
            if update_data.get('dynamic_variable'):
                try:
                    # print(f"🔍 Debug: Saving dynamic variables to local database: {update_data['dynamic_variable']}")
                    AgentModel.update_dynamic_variables(agent_id, update_data['dynamic_variable'])
                    # print(f"✅ Success: Dynamic variables saved to local database")
                except Exception as e:
                    print(f"⚠️ Warning: Failed to save dynamic variables to local database: {str(e)}")

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
    

@ElevenLabsAPIRouter.post("/save-variables", name="save-variables")
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
        
        # Now append variables to prompt and update agent (mimicking edit agent approach)
        if agent.elvn_lab_agent_id:
            try:
                # print(f"🔍 Debug: Updating agent {agent.elvn_lab_agent_id} with new variables")
                
                # Get the current prompt from the agent record
                current_prompt = agent.agent_prompt or ""
                # print(f"🔍 Debug: Current prompt: {current_prompt}")
                
                # Remove existing variable placeholders from the prompt
                import re
                # Remove all {{variable_name}} patterns from the prompt
                base_prompt = re.sub(r'\{\{[^}]+\}\}', '', current_prompt)
                
                # Add new variables to the prompt in {{}} format
                new_variables_text = ""
                for var_name, var_value in variables.items():
                    # Add variables even if they have empty values (ElevenLabs expects this)
                    new_variables_text += f"{{{{{var_name}}}}}"
                
                if new_variables_text:
                    updated_prompt = base_prompt + new_variables_text
                    # print(f"🔍 Debug: Updated prompt with variables: {updated_prompt}")
                    
                    # Update the agent with the new prompt (mimicking edit agent approach)
                    update_result = ElevenLabsAgentCRUD().update_agent(
                        agent_id=agent.elvn_lab_agent_id,
                        prompt=updated_prompt,
                        dynamic_variables=variables
                    )
                    
                    if "error" in update_result:
                        # print(f"❌ Error: Failed to update agent: {update_result}")
                        return JSONResponse(status_code=500, content={
                            "status": "error", 
                            "message": f"Failed to update ElevenLabs agent: {update_result.get('exc', 'Unknown error')}"
                        })
                    else:
                        # print(f"✅ Success: Agent updated with new prompt and variables")
                        pass
                        
                        # Also update the local agent record with the new prompt
                        try:
                            # Update the agent_prompt field in local database
                            from app.databases.models import AgentModel as LocalAgentModel
                            
                            # Use the proper update_prompt method
                            LocalAgentModel.update_prompt(agent_id, updated_prompt)
                            
                            # print(f"✅ Success: Local agent prompt updated with variables")
                        except Exception as local_update_error:
                            # print(f"⚠️ Warning: Failed to update local agent prompt: {str(local_update_error)}")
                            pass
                else:
                    print(f"ℹ️ Info: No new variables to append to prompt")
            except Exception as e:
                print(f"❌ Error: Failed to update ElevenLabs agent: {str(e)}")
                return JSONResponse(status_code=500, content={
                    "status": "error", 
                    "message": f"Failed to update ElevenLabs agent: {str(e)}"
                })
        else:
            print(f"⚠️ Warning: Agent {agent_id} has no elvn_lab_agent_id")
        
        return JSONResponse(status_code=200, content={"status": "success", "message": "Variables saved successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@ElevenLabsAPIRouter.post("/save_widget_customization", name="save-widget-customization")
async def save_widget_customization(request: Request):
    """
    Save widget customization settings for an agent
    """
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        primary_color = data.get("primary_color", "#00d4ff")
        secondary_color = data.get("secondary_color", "#006eff")
        pulse_color = data.get("pulse_color", "rgba(0, 212, 255, 0.3)")
        icon_url = data.get("icon_url", "/static/Web/images/gif-icon-1.gif")
        widget_size = data.get("widget_size", "medium")
        start_btn_color = data.get("start_btn_color", "#1a1a1a")

        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})

        # Get or create agent connection settings
        agent_connection = AgentConnectionModel.get_by_agent_id(agent_id)

        if agent_connection:
            # Update existing connection
            try:
                Session = sessionmaker(bind=engine)
                session = Session()
                agent_connection.primary_color = primary_color
                agent_connection.secondary_color = secondary_color
                agent_connection.pulse_color = pulse_color
                agent_connection.icon_url = icon_url
                # Add widget_size if the column exists, otherwise skip
                if hasattr(agent_connection, 'widget_size'):
                    agent_connection.widget_size = widget_size
                # Add start_btn_color if the column exists
                if hasattr(agent_connection, 'start_btn_color'):
                    agent_connection.start_btn_color = start_btn_color
                session.merge(agent_connection)
                session.commit()
                session.close()
            except Exception as e:
                logger.error(f"Error updating agent connection: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": f"Error updating customization: {str(e)}"})
        else:
            # Create new agent connection
            try:
                Session = sessionmaker(bind=engine)
                session = Session()
                
                new_connection = AgentConnectionModel(
                    agent_id=agent_id,
                    primary_color=primary_color,
                    secondary_color=secondary_color,
                    pulse_color=pulse_color,
                    icon_url=icon_url
                )
                
                # Add widget_size if the column exists
                if hasattr(new_connection, 'widget_size'):
                    new_connection.widget_size = widget_size
                # Add start_btn_color if the column exists
                if hasattr(new_connection, 'start_btn_color'):
                    new_connection.start_btn_color = start_btn_color
                session.add(new_connection)
                session.commit()
                session.close()
                
            except Exception as e:
                logger.error(f"Error creating agent connection: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": f"Error saving customization: {str(e)}"})
        
        return JSONResponse(status_code=200, content={"status": "success", "message": "Widget customization saved successfully"})
        
    except Exception as e:
        logger.error(f"Error in save_widget_customization: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@ElevenLabsAPIRouter.post("/upload_file", name="upload_file")
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
        #Add files to ElevenLabs
        # print(f"🔍 Debug: Uploading file to ElevenLabs: {file.filename}")
        file_info = ElevenLabsAgentCRUD().upload_file_to_knowledge_base(file_path, name=file.filename)
        # print(f"🔍 Debug: ElevenLabs response: {file_info}")
        
        elevenlabs_doc_id = file_info.get("id")
        elevenlabs_doc_name = file_info.get("name")

        KnowledgeBaseFileModel.create(
            knowledge_base_id=knowledge_base_id,
            file_path=temp_file_path,
            file_name=file.filename,
            text_content=text_content,
            elevenlabs_doc_id=elevenlabs_doc_id,
            elevenlabs_doc_name=elevenlabs_doc_name
        )
        
        # Now update ALL agents that use this knowledge base with the new file
        try:
            # Find ALL agents that use this knowledge base
            query = select(agent_knowledge_association).where(
                agent_knowledge_association.c.knowledge_base_id == int(knowledge_base_id)
            )
            
            with db():
                result = db.session.execute(query)
                agent_relations = result.fetchall()  # Get ALL agents
            
            # print(f"🔍 Debug: Found {len(agent_relations)} agents using this knowledge base")
            
            if agent_relations:
                # Process each agent
                for agent_relation in agent_relations:
                    agent_id = agent_relation.agent_id
                    agent = AgentModel.get_by_id(agent_id)
                    
                    if agent and hasattr(agent, 'elvn_lab_agent_id') and agent.elvn_lab_agent_id:
                        # print(f"🔍 Debug: Updating agent {agent.elvn_lab_agent_id} with new file")
                        
                        # Just add the new file to the agent's knowledge base
                        new_file_data = {
                            "id": elevenlabs_doc_id,
                            "name": elevenlabs_doc_name,
                            "type": "file"
                        }

                        old_xi_kb_files = ElevenLabsAgentCRUD().get_agent(agent.elvn_lab_agent_id)
                        
                        if "error" not in old_xi_kb_files:
                            # Extract existing knowledge base files from agent response
                            existing_kb_files = []
                            if (old_xi_kb_files.get("conversation_config") and 
                                old_xi_kb_files["conversation_config"].get("agent") and 
                                old_xi_kb_files["conversation_config"]["agent"].get("prompt") and 
                                old_xi_kb_files["conversation_config"]["agent"]["prompt"].get("knowledge_base")):
                                
                                existing_kb_files = old_xi_kb_files["conversation_config"]["agent"]["prompt"]["knowledge_base"]
                                # print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} has {len(existing_kb_files)} existing knowledge base files")
                            
                            # Add the new file to existing files
                            combined_kb_files = existing_kb_files + [new_file_data]
                            # print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} combined knowledge base files: {combined_kb_files}")
                            
                            # Update the agent with all files
                            update_result = ElevenLabsAgentCRUD().update_agent(
                                agent_id=agent.elvn_lab_agent_id,
                                knowledge_base=combined_kb_files
                            )
                            
                            if "error" in update_result:
                                print(f"❌ Error: Failed to update agent {agent.elvn_lab_agent_id}: {update_result}")
                                raise Exception(f"Failed to update agent {agent.elvn_lab_agent_id}: {update_result}")
                            else:
                                print(f"✅ Success: Agent {agent.elvn_lab_agent_id} updated with new file")
                        else:
                            print(f"❌ Error: Failed to get agent {agent.elvn_lab_agent_id} details: {old_xi_kb_files}")
                            raise Exception(f"Failed to get agent {agent.elvn_lab_agent_id} details: {old_xi_kb_files}")
                    else:
                        print(f"⚠️ Warning: Agent {agent_id} has no elvn_lab_agent_id")
            else:
                print(f"ℹ️ Info: No agents found using this knowledge base")
        
        except Exception as e:
            print(f"❌ Error: Failed to update agent knowledge bases: {str(e)}")
            raise e
        return JSONResponse(status_code=200, content={"status": "success", "message": "File uploaded successfully"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@ElevenLabsAPIRouter.delete("/delete_file", name="delete_file")
async def delete_file(request: Request):
    try:
        data = await request.json()
        file_id = int(data.get("file_id"))
        knowledge_base_id = int(data.get("knowledge_base_id"))
        elevenlabs_doc_id = data.get("elevenlabs_doc_id")
        
        # Validate elevenlabs_doc_id
        if not elevenlabs_doc_id:
            return JSONResponse(status_code=400, content={
                "status": "error", 
                "message": "Missing ElevenLabs document ID"
            })
        
        # Debug logging
        # print(f"🔍 Debug: Deleting file - file_id: {file_id}, knowledge_base_id: {knowledge_base_id}, elevenlabs_doc_id: {elevenlabs_doc_id}")
        
        file = KnowledgeBaseFileModel.get_by_id(file_id)
        if file:
            if file.knowledge_base_id == knowledge_base_id:
                # print(f"🔍 Debug: Found file in database - file_id: {file_id}, knowledge_base_id: {knowledge_base_id}")
                                # Get all files for this knowledge base
                files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base_id)

                # First, remove the file from ALL agents that use this knowledge base
                try:
                    # Find ALL agents that use this knowledge base
                    query = select(agent_knowledge_association).where(
                        agent_knowledge_association.c.knowledge_base_id == int(knowledge_base_id)
                    )
                    
                    with db():
                        result = db.session.execute(query)
                        agent_relations = result.fetchall()  # Get ALL agents
                    
                    # print(f"🔍 Debug: Found {len(agent_relations)} agents using this knowledge base")
                    
                    if agent_relations:
                        # Process each agent
                        for agent_relation in agent_relations:
                            agent_id = agent_relation.agent_id
                            agent = AgentModel.get_by_id(agent_id)
                            
                            if agent and hasattr(agent, 'elvn_lab_agent_id') and agent.elvn_lab_agent_id:
                                # print(f"🔍 Debug: Removing file from agent {agent.elvn_lab_agent_id}")
                                
                                # Get current agent details from ElevenLabs
                                agent_details = ElevenLabsAgentCRUD().get_agent(agent.elvn_lab_agent_id)
                                
                                if "error" not in agent_details:
                                    # Extract existing knowledge base files
                                    existing_kb_files = []
                                    if (agent_details.get("conversation_config") and 
                                        agent_details["conversation_config"].get("agent") and 
                                        agent_details["conversation_config"]["agent"].get("prompt") and 
                                        agent_details["conversation_config"]["agent"]["prompt"].get("knowledge_base")):
                                        
                                        existing_kb_files = agent_details["conversation_config"]["agent"]["prompt"]["knowledge_base"]
                                        # print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} has {len(existing_kb_files)} existing knowledge base files")
                                    
                                    # Remove the file we're deleting
                                    updated_kb_files = [kb_file for kb_file in existing_kb_files if kb_file.get("id") != elevenlabs_doc_id]
                                    # print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} updated KB files (removed {elevenlabs_doc_id}): {updated_kb_files}")
                                    
                                    # Update the agent with the updated knowledge base
                                    update_result = ElevenLabsAgentCRUD().update_agent(
                                        agent_id=agent.elvn_lab_agent_id,
                                        knowledge_base=updated_kb_files
                                    )
                                    
                                    if "error" in update_result:
                                        print(f"❌ Error: Failed to update agent {agent.elvn_lab_agent_id}: {update_result}")
                                        raise Exception(f"Failed to update agent {agent.elvn_lab_agent_id}: {update_result}")
                                    else:
                                        print(f"✅ Success: Agent {agent.elvn_lab_agent_id} updated, file removed from knowledge base")
                                else:
                                    print(f"❌ Error: Failed to get agent {agent.elvn_lab_agent_id} details: {agent_details}")
                                    raise Exception(f"Failed to get agent {agent.elvn_lab_agent_id} details: {agent_details}")
                            else:
                                print(f"⚠️ Warning: Agent {agent_id} has no elvn_lab_agent_id")
                    else:
                        print(f"ℹ️ Info: No agents found using this knowledge base")
                
                except Exception as e:
                    print(f"❌ Error: Failed to update agent knowledge bases: {str(e)}")
                    raise e
                
                # Now delete the file from ElevenLabs KB
                # print(f"🔍 Debug: Attempting to delete file from ElevenLabs with doc_id: {elevenlabs_doc_id}")
                elevenlabs_result = ElevenLabsAgentCRUD().delete_file_from_knowledge_base(elevenlabs_doc_id)
                
                # Check if ElevenLabs deletion was successful
                if elevenlabs_result.get("error"):
                    print(f"❌ Error: Failed to delete file from ElevenLabs: {elevenlabs_result}")
                    return JSONResponse(status_code=500, content={
                        "status": "error", 
                        "message": f"Failed to delete file from ElevenLabs: {elevenlabs_result.get('exc', 'Unknown error')}"
                    })
                
                print(f"✅ Success: File deleted from ElevenLabs successfully")
                
                # Only delete from local storage if ElevenLabs deletion was successful
                try:
                    # print(f"🔍 Debug: Deleting file from local storage with file_id: {file_id}")
                    KnowledgeBaseFileModel.delete(file_id)
                    print(f"✅ Success: File deleted from local storage successfully")
                except Exception as local_delete_error:
                    # If local deletion fails, log the error but don't fail the entire operation
                    # since ElevenLabs deletion was successful
                    print(f"⚠️ Warning: Failed to delete file from local storage: {str(local_delete_error)}")
                
                # If this was the last file, delete the knowledge base too
                if len(files) == 1:  # Only had 1 file which we just deleted
                    try:
                        # print(f"🔍 Debug: Deleting knowledge base with id: {knowledge_base_id}")
                        KnowledgeBaseModel.delete(knowledge_base_id)
                        print(f"✅ Success: Knowledge base deleted successfully")
                        return JSONResponse(status_code=200, content={
                            "status": "success", 
                            "message": "File and knowledge base deleted successfully"
                        })
                    except Exception as kb_delete_error:
                        print(f"⚠️ Warning: Failed to delete knowledge base: {str(kb_delete_error)}")
                        return JSONResponse(status_code=200, content={
                            "status": "success", 
                            "message": "File deleted successfully (knowledge base deletion failed)"
                        })
                
                print(f"✅ Success: File deletion operation completed successfully")
                return JSONResponse(status_code=200, content={
                    "status": "success", 
                    "message": "File deleted successfully"
                })
            else:
                print(f"❌ Error: File knowledge_base_id mismatch - expected: {knowledge_base_id}, actual: {file.knowledge_base_id}")
                return JSONResponse(status_code=400, content={
                    "status": "error", 
                    "message": "File not found in specified knowledge base"
                })
        else:
            print(f"❌ Error: File not found in database with file_id: {file_id}")
            return JSONResponse(status_code=400, content={
                "status": "error", 
                "message": "File not found in database"
            })
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "status": "error", 
            "message": "Something went wrong!", 
            "error": str(e)
        })
    


@ElevenLabsAPIRouter.post("/attach-knowledge-base", name="attach-knowledge-base")
async def attach_knowledge_base(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id")
        knowledge_base_id = data.get("knowledge_base_id")
        
        if agent_id:
            # Convert agent_id to integer for database queries
            try:
                agent_id_int = int(agent_id)
            except ValueError:
                return JSONResponse(status_code=400, content={
                    "status": "error", 
                    "message": "Invalid agent_id format"
                })
                
            agent = AgentModel.get_by_id(agent_id_int)
            if agent:
                # Step 1: Get ElevenLabs Knowledge Base IDs for this specific knowledge base
                try:
                    knowledge_base_files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base_id)
                    
                    # Extract ElevenLabs doc IDs, filtering out any None/empty values
                    elevenlabs_kb_ids = []
                    for file in knowledge_base_files:
                        if file.elevenlabs_doc_id and file.elevenlabs_doc_id.strip():
                            elevenlabs_kb_ids.append(file.elevenlabs_doc_id)
                            # print(f"🔍 Debug: Found file '{file.file_name}' with ElevenLabs doc_id: {file.elevenlabs_doc_id}")
                    
                    # print(f"🔍 Debug: Total files found for KB {knowledge_base_id}: {len(knowledge_base_files)}")
                    # print(f"🔍 Debug: Valid ElevenLabs doc_ids: {len(elevenlabs_kb_ids)}")
                    # print(f"🔍 Debug: ElevenLabs doc_ids: {elevenlabs_kb_ids}")
                    
                    if not elevenlabs_kb_ids:
                        return JSONResponse(status_code=400, content={
                            "status": "error", 
                            "message": f"No valid ElevenLabs files found in knowledge base {knowledge_base_id}"
                        })
                    
                    # print(f"🔍 Debug: Will attach {len(elevenlabs_kb_ids)} files to agent {agent_id}")
                    
                except Exception as kb_error:
                    print(f"❌ Error: Failed to get ElevenLabs KB IDs: {str(kb_error)}")
                    return JSONResponse(status_code=500, content={
                        "status": "error", 
                        "message": f"Failed to retrieve knowledge base files: {str(kb_error)}"
                    })
                
                # Step 2: Update ElevenLabs Agent with the knowledge base files
                try:
                    if hasattr(agent, 'elvn_lab_agent_id') and agent.elvn_lab_agent_id:
                        # Format knowledge base data for ElevenLabs API
                        knowledge_base_data = []
                        for file in knowledge_base_files:
                            if file.elevenlabs_doc_id and file.elevenlabs_doc_id.strip():
                                knowledge_base_data.append({
                                    "name": file.file_name,
                                    "id": file.elevenlabs_doc_id,
                                    "type": "file"
                                })
                        
                        # print(f"🔍 Debug: Formatted knowledge base data for ElevenLabs: {knowledge_base_data}")
                        
                        elevenlabs_result = ElevenLabsAgentCRUD().update_agent(
                            agent_id=agent.elvn_lab_agent_id,
                            knowledge_base=knowledge_base_data
                        )
                        
                        if elevenlabs_result.get("error"):
                            print(f"❌ Error: Failed to update ElevenLabs agent: {elevenlabs_result}")
                            return JSONResponse(status_code=500, content={
                                "status": "error", 
                                "message": f"Failed to update ElevenLabs agent: {elevenlabs_result.get('exc')}"
                            })
                        
                        print(f"✅ Success: ElevenLabs agent {agent.elvn_lab_agent_id} updated with {len(knowledge_base_data)} knowledge base files")
                    else:
                        print(f"⚠️ Warning: Agent {agent_id} has no elvn_lab_agent_id, skipping ElevenLabs update")
                        
                except Exception as elevenlabs_error:
                    print(f"❌ Error: Failed to update ElevenLabs agent: {str(elevenlabs_error)}")
                    return JSONResponse(status_code=500, content={
                        "status": "error", 
                        "message": f"Failed to update ElevenLabs agent: {str(elevenlabs_error)}"
                    })
                
                # Step 3: Update local database association
                try:
                    from sqlalchemy.orm import sessionmaker
                    from app.databases.models import engine
                    from sqlalchemy import insert, select, delete
                    from app.databases.models import agent_knowledge_association
                    Session = sessionmaker(bind=engine)
                    session = Session() 
                
                    # Check if the association already exists
                    query = select(agent_knowledge_association).where(
                        agent_knowledge_association.c.agent_id == agent_id_int
                    )
                    result = session.execute(query)
                    existing_association = result.fetchone()

                    # If the agent has a different knowledge base, delete the old one
                    if existing_association and existing_association.knowledge_base_id != knowledge_base_id:
                        delete_stmt = delete(agent_knowledge_association).where(
                            agent_knowledge_association.c.agent_id == agent_id_int
                        )
                        session.execute(delete_stmt)
                        session.commit()  # Ensure deletion is applied
                        # print(f"🔍 Debug: Removed old knowledge base association for agent {agent_id}")

                    if knowledge_base_id:
                        # If no association exists, insert a new one
                        if not existing_association or existing_association.knowledge_base_id != knowledge_base_id:
                            stmt = insert(agent_knowledge_association).values(
                                agent_id=agent_id_int, 
                                knowledge_base_id=knowledge_base_id
                            )
                            session.execute(stmt)
                            session.commit()
                            # print(f"🔍 Debug: Created new knowledge base association for agent {agent_id}")

                    session.close()
                    
                except Exception as db_error:
                    print(f"❌ Error: Failed to update local database: {str(db_error)}")
                    return JSONResponse(status_code=500, content={
                        "status": "error", 
                        "message": f"Failed to update local database: {str(db_error)}"
                    })

                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "success", 
                        "message": f"Agent updated successfully with {len(elevenlabs_kb_ids)} knowledge base files", 
                        "status_code": 200,
                        "elevenlabs_files_attached": len(elevenlabs_kb_ids)
                    }
                )
            else:
                return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Agent details is not exist!"})
    except Exception as e:
        print(f"❌ Error: Unexpected error in attach_knowledge_base: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})
    

@ElevenLabsAPIRouter.post("/upload_knowledge_base", name="upload_knowledge_base")
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
            
            file_info = ElevenLabsAgentCRUD().upload_file_to_knowledge_base(file_path, name=attachment.filename)
            elevenlabs_doc_id = file_info.get("id")
            elevenlabs_doc_name = file_info.get("name")

            # Save file details to database
            KnowledgeBaseFileModel.create(
                knowledge_base_id=knowledge_base.id,
                file_name=attachment.filename,
                file_path=temp_file_path,
                text_content=text_content,
                elevenlabs_doc_id=elevenlabs_doc_id,
                elevenlabs_doc_name=elevenlabs_doc_name
            )
            content_list.append({
                "file_path": temp_file_path,
                "text_content": text_content
            })
            uploaded_files.append(attachment.filename)

        # Local vector store creation removed - using ElevenLabs knowledge base directly
        # splits = get_splits(content_list)
        # vector_id = str(uuid.uuid4())
        # if splits:
        #     status, vector_path =convert_to_vectorstore(splits, vector_id)
        #     KnowledgeBaseModel.update(knowledge_base.id, vector_path=vector_path, vector_id=vector_id)
        
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
    
@ElevenLabsAPIRouter.delete("/delete_knowledge_base", name="delete_knowledge_base")
async def delete_knowledge_base(request: Request):
    data = await request.json()
    knowledge_base_id = data.get("knowledge_base_id")
    elevenlabs_doc_id = data.get("elevenlabs_doc_id")
    elevenlabs_result = ElevenLabsAgentCRUD().delete_file_from_knowledge_base(elevenlabs_doc_id)
    if elevenlabs_result.get("error"):
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete file from ElevenLabs!", "error": elevenlabs_result.get("exc")})
    KnowledgeBaseModel.delete(knowledge_base_id)
    return JSONResponse(status_code=200, content={"status": "success", "message": "Knowledge base deleted successfully"})



@ElevenLabsAPIRouter.post("/custom-functions", name="custom-functions")
async def create_custom_function(request: Request):
    try:
        data = await request.json()
        form_data = data.get("webhook_config", {})
        agent_id = data.get("agent_id")
        agent = AgentModel.get_by_id(agent_id)
        if not agent:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found"})
        
        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})
        
        if not form_data:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Webhook configuration is required"})
        
        eleven_agent_id = agent.elvn_lab_agent_id
        # print(f"Debug: Local agent ID: {agent_id}")
        # print(f"Debug: ElevenLabs agent ID: '{eleven_agent_id}' (type: {type(eleven_agent_id)})")
        
        if not eleven_agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "ElevenLabs agent ID is required. Please ensure the agent is properly created in ElevenLabs."})
        
        # Validate required fields
        tool_name = form_data.get("tool_name", "")
        tool_description = form_data.get("tool_description", "")
        
        # print(f"Debug: Received form_data: {form_data}")
        # print(f"Debug: tool_name: '{tool_name}' (type: {type(tool_name)})")
        # print(f"Debug: tool_description: '{tool_description}' (type: {type(tool_description)})")
        
        if not tool_name or not tool_description:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Tool name and description are required"})
        
        # Ensure tool_name is a string and not None
        if tool_name is None:
            tool_name = ""
        
        # Validate tool name format (ElevenLabs requirements: ^[a-zA-Z0-9_-]{1,64}$)
        if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', str(tool_name)):
            return JSONResponse(
                status_code=400, 
                content={"status": "error", "message": "Invalid tool name. Must contain only letters, numbers, underscores, and hyphens. Max 64 characters."}
            )
        
        # Check if tool name already exists for this agent (local database)
        from app.databases.models import ElevenLabsWebhookToolModel
        existing_tool = ElevenLabsWebhookToolModel.get_by_name(tool_name, agent_id)
        if existing_tool:
            return JSONResponse(status_code=400, content={
                "status": "error", 
                "message": f"A webhook tool with the name '{tool_name}' already exists for this agent."
            })
        
        # Check if tool name already exists in our database for this agent
        try:
            existing_tool = ElevenLabsWebhookToolModel.get_by_name(tool_name, agent_id)
            if existing_tool:
                return JSONResponse(status_code=400, content={
                    "status": "error", 
                    "message": f"A webhook tool with the name '{tool_name}' already exists for this agent."
                })
        except Exception as e:
            print(f"Warning: Could not check existing tools in database: {e}")
            # Continue with creation - this is not a critical error
        
        # Build ElevenLabs tool_config structure using the fixed function
        try:
            tool_config = build_elevenlabs_tool_config(form_data)
            print(f"✅ Successfully built tool config for creation: {form_data.get('tool_name', 'Unknown')}")
        except ValueError as ve:
            print(f"❌ Validation error building tool config: {str(ve)}")
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"Validation error: {str(ve)}",
                    "error": str(ve)
                }
            )
        except Exception as e:
            print(f"❌ Error building tool config: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Failed to build tool configuration",
                    "error": str(e)
                }
            )
        
        # print(f"Debug: Built ElevenLabs tool_config: {json.dumps(tool_config, indent=2)}")
        
        # Validate that POST/PUT/PATCH methods have request_body_schema if needed
        api_schema = tool_config.get("api_schema", {})
        http_method = api_schema.get("method", "")
        request_body_schema = api_schema.get("request_body_schema")
        
        # print(f"Debug: HTTP method: '{http_method}'")
        # print(f"Debug: Request body schema exists: {request_body_schema is not None}")
        
        # Create the tool in ElevenLabs
        result = ElevenLabsAgentCRUD().create_webhook_function(tool_config)  # Pass tool_config directly
        
        if "error" in result:
            return JSONResponse(status_code=500, content={
                "status": "error", 
                "message": f"Failed to create webhook function: {result.get('exc', 'Unknown error')}"
            })
        
        # Extract ElevenLabs tool ID from result
        elevenlabs_tool_id = result.get("id") or result.get("tool_id")
        
        # print(f"Debug: Created tool in ElevenLabs with ID: {elevenlabs_tool_id}")
        
        if not elevenlabs_tool_id:
            return JSONResponse(status_code=500, content={
                "status": "error", 
                "message": "Failed to get tool ID from ElevenLabs response"
            })
        
        # Get existing tools from agent's conversation config and add the new tool
        # print(f"Debug: Getting existing tools for agent {eleven_agent_id}")
        existing_agent_result = ElevenLabsAgentCRUD().get_agent(eleven_agent_id)
        
        existing_tool_ids = []
        if "error" not in existing_agent_result:
            # Extract tool_ids from agent's conversation config
            conversation_config = existing_agent_result.get("conversation_config", {})
            agent_config = conversation_config.get("agent", {})
            prompt_config = agent_config.get("prompt", {})
            existing_tool_ids = prompt_config.get("tool_ids", [])
            # print(f"Debug: Found {len(existing_tool_ids)} existing tools in agent config: {existing_tool_ids}")
        else:
            print(f"Warning: Failed to get agent config: {existing_agent_result}")
        
        # Add the new tool to the existing tools list
        all_tool_ids = existing_tool_ids + [elevenlabs_tool_id]
        # print(f"Debug: Updating agent {eleven_agent_id} with all tools: {all_tool_ids}")
        
        update_result = ElevenLabsAgentCRUD().update_agent_tools(eleven_agent_id, all_tool_ids)
        
        if "error" in update_result:
            print(f"Warning: Failed to attach tool to agent: {update_result.get('exc')}")
            # The tool exists in ElevenLabs but isn't attached to the agent
        
        # Save to local database
        try:
            local_tool = ElevenLabsWebhookToolModel.create(
                agent_id=agent_id,
                tool_name=tool_name,
                tool_description=tool_description,
                tool_config={"tool_config": tool_config},  # Wrap for local storage
                elevenlabs_tool_id=elevenlabs_tool_id
            )
            
            print(f"Success: Saved webhook tool to local database with ID: {local_tool.id}")
            
            # Prepare response data
            response_data = {
                "id": local_tool.id,
                "tool_name": local_tool.tool_name,
                "tool_description": local_tool.tool_description,
                "elevenlabs_tool_id": local_tool.elevenlabs_tool_id,
                "tool_config": local_tool.tool_config
            }
            
            return JSONResponse(status_code=200, content={
                "status": "success", 
                "message": "Webhook tool created successfully",
                "data": response_data
            })
            
        except Exception as db_error:
            print(f"Error: Failed to save to local database: {str(db_error)}")
            # Even if local save fails, we still return success since ElevenLabs creation succeeded
            return JSONResponse(status_code=200, content={
                "status": "success", 
                "message": "Webhook tool created in ElevenLabs successfully (local save failed)",
                "data": result
            })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


def build_elevenlabs_tool_config(form_data: dict) -> dict:
    """
    Build a WebhookToolConfig that matches ElevenLabs expected format exactly.
    Fixed based on actual API validation errors.
    """
    import re
    from typing import Any, Dict

    # Extract basic fields
    tool_name = form_data.get("tool_name", "")
    tool_description = form_data.get("tool_description", "")
    api_url = form_data.get("api_url", "")
    http_method = form_data.get("http_method", "POST").upper()
    
    try:
        response_timeout = int(form_data.get("response_timeout", 20))
    except (ValueError, TypeError):
        response_timeout = 20
    
    # Extract URL placeholders from the API URL
    url_placeholders = set()
    if api_url:
        import re
        # Find all placeholders in the URL like {username}, {id}, etc.
        placeholder_pattern = r'\{([^}]+)\}'
        url_placeholders = set(re.findall(placeholder_pattern, api_url))
        print(f"🔍 Debug: Found URL placeholders: {url_placeholders}")
    
    # Build path_params_schema - ElevenLabs expects a DICTIONARY, not array
    path_params_schema = {}
    path_params = form_data.get("path_params", [])
    
    # Create a set of defined parameter names
    defined_param_names = set()
    for param in path_params:
        param_name = param.get("name", "")
        param_type = param.get("type", "string")
        param_description = param.get("description", "")
        param_dynamic_var = param.get("dynamic_variable", "")
        param_constant_value = param.get("constant_value", "")
        param_required = param.get("required", False)
        
        if param_name:
            param_obj = {
                "type": param_type,
                "description": param_description or f"Path parameter {param_name}"
            }
            
            # Add dynamic_variable or constant_value if provided
            if param_dynamic_var:
                param_obj["dynamic_variable"] = param_dynamic_var
            elif param_constant_value:
                param_obj["constant_value"] = param_constant_value
                
            path_params_schema[param_name] = param_obj
            defined_param_names.add(param_name)
    
    # Check if all URL placeholders have corresponding path parameters
    missing_params = url_placeholders - defined_param_names
    if missing_params:
        print(f"⚠️ Warning: URL placeholders {missing_params} don't have corresponding path parameters")
        print(f"🔍 Debug: URL placeholders: {url_placeholders}")
        print(f"🔍 Debug: Defined parameters: {defined_param_names}")
        
        # Auto-create missing path parameters with default values
        for missing_param in missing_params:
            print(f"🔧 Auto-creating missing path parameter: {missing_param}")
            path_params_schema[missing_param] = {
                "type": "string",
                "description": f"Path parameter {missing_param} (auto-generated)"
            }
    
    # Build query_params_schema - ElevenLabs expects properties as DICTIONARY, not array
    # And "type" field is not allowed at root level
    query_params_schema = None
    query_params_properties = {}
    
    query_params = form_data.get("query_params", [])
    for param in query_params:
        param_name = param.get("name", "")
        param_type = param.get("type", "string")
        param_description = param.get("description", "")
        param_dynamic_var = param.get("dynamic_variable", "")
        param_constant_value = param.get("constant_value", "")
        param_required = param.get("required", False)
        
        if param_name:
            param_obj = {
                "type": param_type,
                "description": param_description or f"Query parameter {param_name}"
            }
            
            # Add dynamic_variable or constant_value if provided
            if param_dynamic_var:
                param_obj["dynamic_variable"] = param_dynamic_var
            elif param_constant_value:
                param_obj["constant_value"] = param_constant_value
                
            query_params_properties[param_name] = param_obj
    
    # Only create query_params_schema if there are actual query parameters
    if query_params_properties:
        query_params_schema = {"properties": query_params_properties}
        # print(f"🔍 Debug: Created query_params_schema with {len(query_params_properties)} properties")
    else:
        # print(f"🔍 Debug: No query parameters found, query_params_schema will be None")
        pass
    
    # Build request_body_schema (only for POST/PUT/PATCH methods)
    request_body_schema = None
    if http_method in ["POST", "PUT", "PATCH"]:
        request_body_properties = form_data.get("request_body_properties", [])
        body_description = form_data.get("body_description", "")
        
        if request_body_properties or body_description:
            # Build properties as DICTIONARY, not array
            properties_dict = {}
            required_fields = []
            
            for prop in request_body_properties:
                prop_name = prop.get("name", "")
                prop_type = prop.get("type", "string")
                prop_description = prop.get("description", "")
                prop_required = prop.get("required", False)
                prop_dynamic_var = prop.get("dynamic_variable", "")
                prop_constant_value = prop.get("constant_value", "")
                
                if prop_name:
                    prop_obj = {
                        "type": prop_type,
                        "description": prop_description or f"Property {prop_name}"
                    }
                    
                    # Add dynamic_variable or constant_value if provided
                    if prop_dynamic_var:
                        prop_obj["dynamic_variable"] = prop_dynamic_var
                    elif prop_constant_value:
                        prop_obj["constant_value"] = prop_constant_value
                    
                    properties_dict[prop_name] = prop_obj
                    
                    if prop_required:
                        required_fields.append(prop_name)
            
            # Build request_body_schema without extra fields
            request_body_schema = {
                "type": "object",
                "description": body_description or "Request body",
                "properties": properties_dict,
                "required": required_fields  # This should be a LIST, not boolean
            }
    
    # Build request_headers - ElevenLabs expects DICTIONARY, not array
    request_headers = {}
    headers_data = form_data.get("request_headers", [])
    for header in headers_data:
        header_name = header.get("name", "")
        header_value = header.get("value", "")
        header_type = header.get("type", "string")
        
        if header_name and header_value:
            if header_type == "secret":
                request_headers[header_name] = {
                    "type": "secret",
                    "secret_id": header_value
                }
            elif header_type == "dynamic_variable":
                request_headers[header_name] = {
                    "variable_name": header_value
                }
            else:
                request_headers[header_name] = header_value
    
    # Build dynamic_variables
    dynamic_variable_placeholders = {}
    dynamic_vars = form_data.get("dynamic_variables", [])
    for var in dynamic_vars:
        var_name = var.get("name", "")
        var_value = var.get("value", "")
        if var_name:
            # Try to parse as appropriate type
            try:
                if isinstance(var_value, str):
                    if var_value.lower() in ['true', 'false']:
                        dynamic_variable_placeholders[var_name] = var_value.lower() == 'true'
                    elif '.' in var_value:
                        dynamic_variable_placeholders[var_name] = float(var_value)
                    elif var_value.isdigit():
                        dynamic_variable_placeholders[var_name] = int(var_value)
                    else:
                        dynamic_variable_placeholders[var_name] = var_value
                else:
                    dynamic_variable_placeholders[var_name] = var_value
            except (ValueError, AttributeError):
                dynamic_variable_placeholders[var_name] = var_value
    
    # Build assignments
    assignments = []
    assignments_data = form_data.get("assignments", [])
    for assignment in assignments_data:
        dynamic_var = assignment.get("dynamic_variable", "")
        value_path = assignment.get("value_path", "")
        source = assignment.get("source", "response")
        
        if dynamic_var and value_path:
            assignments.append({
                "dynamic_variable": dynamic_var,
                "value_path": value_path,
                "source": source
            })
    
    # Handle force_pre_tool_speech - convert string values to boolean if needed
    force_pre_tool_speech = form_data.get("force_pre_tool_speech", False)
    if isinstance(force_pre_tool_speech, str):
        if force_pre_tool_speech.lower() in ['true', '1', 'yes', 'on']:
            force_pre_tool_speech = True
        else:
            force_pre_tool_speech = False
    # print(f"🔍 Debug: force_pre_tool_speech: {force_pre_tool_speech} (type: {type(force_pre_tool_speech)})")
    
    # Handle disable_interruptions - convert string values to boolean if needed  
    disable_interruptions = form_data.get("disable_interruptions", False)
    if isinstance(disable_interruptions, str):
        if disable_interruptions.lower() in ['true', '1', 'yes', 'on']:
            disable_interruptions = True
        else:
            disable_interruptions = False
    # print(f"🔍 Debug: disable_interruptions: {disable_interruptions} (type: {type(disable_interruptions)})")
    
    # Build the tool_config matching ElevenLabs exact format
    tool_config = {
        "type": "webhook",
        "name": tool_name,
        "description": tool_description,
        "api_schema": {
            "url": api_url,
            "method": http_method,
            "request_headers": request_headers,
            "auth_connection": form_data.get("auth_connection")  # Can be null
        },
        "response_timeout_secs": response_timeout,
        "dynamic_variables": {
            "dynamic_variable_placeholders": dynamic_variable_placeholders
        },
        "assignments": assignments,
        "disable_interruptions": disable_interruptions,
        "force_pre_tool_speech": force_pre_tool_speech
    }
    
    # Only add path_params_schema if it has content
    if path_params_schema:
        tool_config["api_schema"]["path_params_schema"] = path_params_schema
        
        # Final validation: Ensure all URL placeholders have corresponding path parameters
        final_defined_params = set(path_params_schema.keys())
        still_missing = url_placeholders - final_defined_params
        if still_missing:
            print(f"❌ Error: Still missing path parameters for URL placeholders: {still_missing}")
            raise ValueError(f"URL placeholders {still_missing} must have corresponding path parameters defined")
        else:
            print(f"✅ Validation passed: All URL placeholders have corresponding path parameters")
            print(f"🔍 Debug: URL placeholders: {url_placeholders}")
            print(f"🔍 Debug: Path parameters: {final_defined_params}")
    
    # Only add query_params_schema if it has actual parameters
    if query_params_schema and query_params_schema.get("properties") and len(query_params_schema.get("properties", {})) > 0:
        # print(f"🔍 Debug: Adding query_params_schema to tool_config with {len(query_params_schema.get('properties', {}))} properties")
        tool_config["api_schema"]["query_params_schema"] = query_params_schema
    else:
        # print(f"🔍 Debug: Not adding query_params_schema - query_params_schema: {query_params_schema}")
        pass
    
    # Only add request_body_schema if it exists
    if request_body_schema:
        tool_config["api_schema"]["request_body_schema"] = request_body_schema
    
    # print(f"🔍 Debug: Final tool_config api_schema keys: {list(tool_config['api_schema'].keys())}")
    if 'query_params_schema' in tool_config['api_schema']:
        # print(f"🔍 Debug: query_params_schema in final config: {tool_config['api_schema']['query_params_schema']}")
        pass
    
    return tool_config

@ElevenLabsAPIRouter.delete("/delete-custom-functions", name="delete-custom-functions")
async def delete_custom_functions(request: Request):
    try:
        data = await request.json()
        function_id = data.get("function_id")
        
        if not function_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Function ID is required"})
        
        
        
        # Get the tool first to get the ElevenLabs ID and agent ID
        tool = ElevenLabsWebhookToolModel.get_by_id(function_id)
        if not tool:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Webhook tool not found"})
        
        # print(f"🔍 Debug: Deleting tool {function_id} with ElevenLabs ID: {tool.elevenlabs_tool_id}")
        
        # Get the agent to get the ElevenLabs agent ID
        agent = AgentModel.get_by_id(tool.agent_id)
        if not agent or not agent.elvn_lab_agent_id:
            print(f"⚠️ Warning: Agent or ElevenLabs agent ID not found for agent {tool.agent_id}")
        else:
            # Step 1: Remove tool from agent's tool list in ElevenLabs
            try:
                elevenlabs_agent = ElevenLabsAgentCRUD().get_agent(agent.elvn_lab_agent_id)
                if "error" not in elevenlabs_agent:
                    # Extract tool IDs from agent response
                    conversation_config = elevenlabs_agent.get("conversation_config", {})
                    agent_config = conversation_config.get("agent", {})
                    prompt_config = agent_config.get("prompt", {})
                    existing_tool_ids = prompt_config.get("tool_ids", [])
                    
                    # Remove the tool ID from the list
                    updated_tool_ids = [tid for tid in existing_tool_ids if tid != tool.elevenlabs_tool_id]
                    
                    # print(f"🔍 Debug: Updating agent {agent.elvn_lab_agent_id} with tools: {updated_tool_ids}")
                    
                    # Update agent with remaining tools
                    update_result = ElevenLabsAgentCRUD().update_agent_tools(agent.elvn_lab_agent_id, updated_tool_ids)
                    if "error" in update_result:
                        print(f"⚠️ Warning: Failed to remove tool from agent: {update_result.get('exc')}")
                    else:
                        print(f"✅ Success: Removed tool from agent")
                        
            except Exception as el_error:
                print(f"⚠️ Warning: Failed to update agent tools: {str(el_error)}")
        
        # Step 2: Delete the tool from ElevenLabs (if we have the tool ID)
        if tool.elevenlabs_tool_id:
            try:
                delete_result = ElevenLabsAgentCRUD().delete_webhook_function(tool.elevenlabs_tool_id)
                if "error" in delete_result:
                    print(f"⚠️ Warning: Failed to delete tool from ElevenLabs: {delete_result.get('exc')}")
                else:
                    print(f"✅ Success: Deleted tool from ElevenLabs")
            except Exception as el_error:
                print(f"⚠️ Warning: Failed to delete tool from ElevenLabs: {str(el_error)}")
        
        # Step 3: Delete from local database
        success = ElevenLabsWebhookToolModel.delete(function_id)
        
        if success:
            print(f"✅ Success: Deleted webhook tool from local database")
            return JSONResponse(status_code=200, content={"status": "success", "message": "Webhook tool deleted successfully"})
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete webhook tool from database"})
            
    except Exception as e:
        print(f"❌ Error: Failed to delete webhook tool: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})
    



@ElevenLabsAPIRouter.get("/get-custom-functions", name="get-custom-functions")
async def get_custom_functions(request: Request):
    try:
        function_id = request.query_params.get('function_id')
        
        # Validate function_id parameter
        if not function_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Function ID is required"})
        
        # Convert to integer if possible
        try:
            function_id = int(function_id)
        except ValueError:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Function ID must be a valid integer"})
        
        function = ElevenLabsWebhookToolModel.get_by_id(function_id)
        if function:
            # print(f"🔍 Debug: get_custom_functions - Raw database values:")
            # print(f"🔍 Debug: - function.tool_name: '{function.tool_name}'")
            # print(f"🔍 Debug: - function.tool_description: '{function.tool_description}'")
            # print(f"🔍 Debug: - function.tool_config type: {type(function.tool_config)}")
            # print(f"🔍 Debug: - function.tool_config: {function.tool_config}")
            
            # Extract api_url and timeout from tool_config
            tool_config = function.tool_config or {}
            
            # Check if tool_config has nested structure
            if 'tool_config' in tool_config:
                inner_config = tool_config['tool_config']
                api_schema = inner_config.get('api_schema', {})
                api_url = api_schema.get('url', '')
                response_timeout = inner_config.get('response_timeout_secs', 30)
            else:
                # Direct structure fallback
                api_url = tool_config.get('api_url', '')
                response_timeout = tool_config.get('response_timeout_secs', 30)
            
            function_data = {
                "id": function.id,
                "function_name": function.tool_name,
                "function_description": function.tool_description,
                "function_url": api_url,
                "function_timeout": response_timeout,
                "function_parameters": function.tool_config
            }
            
            # print(f"🔍 Debug: get_custom_functions returning data for function_id {function_id}:")
            # print(f"🔍 Debug: - id: {function_data['id']}")
            # print(f"🔍 Debug: - function_name: '{function_data['function_name']}'")
            # print(f"🔍 Debug: - function_description: '{function_data['function_description']}'")
            # print(f"🔍 Debug: - function_url: '{function_data['function_url']}'")
            # print(f"🔍 Debug: - function_timeout: {function_data['function_timeout']}")
            # print(f"🔍 Debug: - function_parameters keys: {list(function_data['function_parameters'].keys()) if function_data['function_parameters'] else 'None'}")
            
            response = {
                "status": "success",
                "message": "Custom functions fetched successfully",
                "data": function_data
            }
            return JSONResponse(status_code=200, content=response)
        else:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Custom function not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})
    




@ElevenLabsAPIRouter.put("/edit-custom-functions/{function_id}", name="edit-custom-functions")
async def edit_custom_functions(function_id: int, request: Request):
    try:
        data = await request.json()
        function_name = data.get("function_name")
        function_description = data.get("function_description")
        function_url = data.get("function_url")
        function_timeout = data.get("function_timeout")
        function_parameters = data.get("function_parameters", {})
        agent_id = data.get("agent_id")
        
        # Validate required fields
        if not function_name:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Function name is required"})
        
        if not function_description:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Function description is required"})
        
        if not function_url:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Function URL is required"})
        
        # Get the existing function and agent within a single database session
        with db():
            function = ElevenLabsWebhookToolModel.get_by_id(function_id)
            if not function:
                return JSONResponse(status_code=404, content={"status": "error", "message": "Custom function not found"})
            
            # Get the agent to get the ElevenLabs agent ID
            agent = AgentModel.get_by_id(function.agent_id)
            if not agent or not agent.elvn_lab_agent_id:
                return JSONResponse(status_code=400, content={"status": "error", "message": "Agent not found or not linked to ElevenLabs"})
        
        # Extract the existing tool configuration to get the current structure
        existing_tool_config = function.tool_config or {}
        
        # Handle nested structure - extract the inner tool_config if it exists
        if 'tool_config' in existing_tool_config:
            inner_config = existing_tool_config['tool_config']
            # Extract existing parameters for merging
            existing_api_schema = inner_config.get('api_schema', {})
            existing_params = {
                'tool_name': function_name,
                'tool_description': function_description,
                'api_url': function_url,
                'http_method': existing_api_schema.get('method', 'POST'),
                'response_timeout': function_timeout or 20,
                'body_description': existing_api_schema.get('request_body_schema', {}).get('description', ''),
                'request_body_properties': [],
                'query_params': [],
                'path_params': [],
                'request_headers': [],
                'dynamic_variables': [],
                'assignments': []
            }
            
            # Extract request body properties if they exist
            request_body_schema = existing_api_schema.get('request_body_schema', {})
            if request_body_schema and 'properties' in request_body_schema:
                for prop_name, prop_config in request_body_schema['properties'].items():
                    existing_params['request_body_properties'].append({
                        'name': prop_name,
                        'type': prop_config.get('type', 'string'),
                        'description': prop_config.get('description', ''),
                        'required': prop_name in request_body_schema.get('required', []),
                        'dynamic_variable': prop_config.get('dynamic_variable', ''),
                        'constant_value': prop_config.get('constant_value', '')
                    })
        else:
            # Direct structure - use function_parameters as is but update with new values
            existing_params = function_parameters.copy()
            existing_params.update({
                'tool_name': function_name,
                'tool_description': function_description,
                'api_url': function_url,
                'response_timeout': function_timeout or 20
            })
        
        # Build ElevenLabs tool config using the merged parameters
        try:
            tool_config = build_elevenlabs_tool_config(existing_params)
            print(f"✅ Successfully built tool config for function: {function_name}")
        except ValueError as ve:
            print(f"❌ Validation error building tool config: {str(ve)}")
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": f"Validation error: {str(ve)}",
                    "error": str(ve)
                }
            )
        except Exception as e:
            print(f"❌ Error building tool config: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": "Failed to build tool configuration",
                    "error": str(e)
                }
            )
        
        # Update tool in ElevenLabs using ElevenLabsAgentCRUD
        try:
            elevenlabs_crud = ElevenLabsAgentCRUD()
            result = elevenlabs_crud.update_webhook_tool(function.elevenlabs_tool_id, tool_config)
            
            if "error" in result:
                print(f"❌ Error updating ElevenLabs tool: {result}")
                return JSONResponse(
                    status_code=500, 
                    content={
                        "status": "error", 
                        "message": "Failed to update tool in ElevenLabs", 
                        "error": result.get("exc", "Unknown error")
                    }
                )
            else:
                print(f"✅ Successfully updated ElevenLabs tool: {result}")
            
        except Exception as elevenlabs_error:
            print(f"❌ Error updating ElevenLabs tool: {elevenlabs_error}")
            return JSONResponse(
                status_code=500, 
                content={
                    "status": "error", 
                    "message": "Failed to update tool in ElevenLabs", 
                    "error": str(elevenlabs_error)
                }
            )
        
        # Update the function in database within the same session
        with db():
            # Re-fetch the function to ensure we have a fresh instance in this session
            function = ElevenLabsWebhookToolModel.get_by_id(function_id)
            if not function:
                return JSONResponse(status_code=404, content={"status": "error", "message": "Custom function not found"})
            
            # print(f"🔍 Debug: Updating local database with:")
            # print(f"🔍 Debug: - function_name: '{function_name}'")
            # print(f"🔍 Debug: - function_description: '{function_description}'")
            # print(f"🔍 Debug: - function_parameters: {function_parameters}")
            
            # Store old values for comparison
            old_name = function.tool_name
            old_description = function.tool_description
            old_config = function.tool_config
            
            function.tool_name = function_name
            function.tool_description = function_description
            # Store in the same nested structure as creation for consistency
            function.tool_config = {"tool_config": tool_config}
            
            # print(f"🔍 Debug: Database changes:")
            # print(f"🔍 Debug: - tool_name: '{old_name}' → '{function.tool_name}'")
            # print(f"🔍 Debug: - tool_description: '{old_description}' → '{function.tool_description}'")
            # print(f"🔍 Debug: - tool_config changed: {old_config != function.tool_config}")
            
            # Explicitly add the object to the session to ensure it's tracked
            db.session.add(function)
            # print(f"🔍 Debug: Object added to session")
            
            # Commit the changes
            db.session.commit()
            # print(f"✅ Debug: Database commit successful")
            
            # Force a flush to ensure changes are written to database
            db.session.flush()
            # print(f"✅ Debug: Database flush completed")
            
            # Verify the data was actually saved by reading it back
            verification_function = ElevenLabsWebhookToolModel.get_by_id(function_id)
            # print(f"🔍 Debug: Verification read - tool_description: '{verification_function.tool_description}'")
            
            # Also check if the object is dirty (has uncommitted changes)
            # print(f"🔍 Debug: Function object dirty: {db.session.dirty}")
            # print(f"🔍 Debug: Function object new: {db.session.new}")
            # print(f"🔍 Debug: Function object deleted: {db.session.deleted}")
            
            # Prepare response data using the verification function
            # Extract URL and timeout from the stored nested structure
            stored_config = verification_function.tool_config or {}
            if 'tool_config' in stored_config:
                inner_config = stored_config['tool_config']
                api_schema = inner_config.get('api_schema', {})
                response_url = api_schema.get('url', function_url)
                response_timeout = inner_config.get('response_timeout_secs', function_timeout or 20)
            else:
                response_url = function_url
                response_timeout = function_timeout or 20
                
            function_data = {
                "id": verification_function.id,
                "function_name": verification_function.tool_name,
                "function_description": verification_function.tool_description,
                "function_url": response_url,
                "function_timeout": response_timeout,
                "function_parameters": verification_function.tool_config
            }
        
        # print(f"🔍 Debug: Response data being sent to frontend:")
        # print(f"🔍 Debug: - id: {function_data['id']}")
        # print(f"🔍 Debug: - function_name: '{function_data['function_name']}'")
        # print(f"🔍 Debug: - function_description: '{function_data['function_description']}'")
        # print(f"🔍 Debug: - function_url: '{function_data['function_url']}'")
        # print(f"🔍 Debug: - function_timeout: {function_data['function_timeout']}")
        
        response = {
            "status": "success",
            "message": "Custom function updated successfully",
            "data": function_data
        }
        
        return JSONResponse(status_code=200, content=response)
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})


@ElevenLabsAPIRouter.post("/add_url", name="add_url")
async def add_url(request: Request):
    temp_file_path = None
    knowledge_base = None
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



        from elevenlabs import ElevenLabs
        client = ElevenLabs(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
        )
        resp = client.conversational_ai.knowledge_base.documents.create_from_url(
            url=url,
            name=name
        )
        

        # Check if ElevenLabs document creation was successful
        if not resp or "error" in resp:
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"Failed to create document in ElevenLabs: {resp.get('error', 'Unknown error')}"}
            )

        elevenlabs_doc_id = resp.id
        elevenlabs_doc_name = resp.name

        # Check if we got valid document ID and name from ElevenLabs
        if not elevenlabs_doc_id or not elevenlabs_doc_name or not str(elevenlabs_doc_id).strip() or not str(elevenlabs_doc_name).strip():
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": "ElevenLabs returned invalid document ID or name - no local file will be saved"}
            )
        
        # Only proceed with text extraction and database save if ElevenLabs document creation was successful
        base_url = "https://api.elevenlabs.io/v1/convai/knowledge-base"
        url = f"{base_url}/{elevenlabs_doc_id}"

        headers = {
                "xi-api-key": os.getenv("ELEVENLABS_API_KEY"),
                "Content-Type": "application/json"
            }

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            print("✅ Successfully fetched document data!")
            html_content = data["extracted_inner_html"]
    
            # Clean it with BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            clean_text = soup.get_text(separator="\n", strip=True)
            
            print("🧹 Cleaned Text:\n")
            print(clean_text)

            # Save cleaned text to the temp file that was created earlier
            with open(temp_file_path, "w", encoding="utf-8") as f:
                f.write(clean_text)
            
            # Save to database with ElevenLabs document details
            KnowledgeBaseFileModel.create(
                knowledge_base_id=knowledge_base.id,
                file_name=name,
                file_path=temp_file_path,
                text_content=clean_text,
                elevenlabs_doc_id=elevenlabs_doc_id,
                elevenlabs_doc_name=elevenlabs_doc_name
            )
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success", 
                    "message": "URL added to knowledge base successfully", 
                    "document_id": elevenlabs_doc_id,
                    "file_name": name
                }
            )
        else:
            print("❌ Error fetching document data:")
            print("Response:", response.text)
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"Failed to extract text from URL: {response.text}"}
            )

    except Exception as e:
        # Cleanup on error
        if knowledge_base:
            files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base.id)
            for file in files:
                KnowledgeBaseFileModel.delete(file.id)
            KnowledgeBaseModel.delete(knowledge_base.id)
        
        # Cleanup temp file on error
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        
        print("Error:", str(e))
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

@ElevenLabsAPIRouter.post("/create_text", name="create_text")
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

        from elevenlabs import ElevenLabs

        client = ElevenLabs(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
        )
        resp = client.conversational_ai.knowledge_base.documents.create_from_text(
            text=content,
            name=title
        )
        if not resp or "error" in resp:
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": f"Failed to create document in ElevenLabs: {resp.get('error', 'Unknown error')}"}
            )
        
        elevenlabs_doc_id = resp.id
        elevenlabs_doc_name = resp.name

        # Check if we got valid document ID and name from ElevenLabs
        if not elevenlabs_doc_id or not elevenlabs_doc_name or not str(elevenlabs_doc_id).strip() or not str(elevenlabs_doc_name).strip():
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": "ElevenLabs returned invalid document ID or name - no local file will be saved"}
            )

        # Only create local file if ElevenLabs was successful
        with open(file_path, "w") as file:
            file.write(content)

        # Save file details to database with ElevenLabs document details
        KnowledgeBaseFileModel.create(
            knowledge_base_id=knowledge_base.id,
            file_name=title,
            file_path=file_path,
            text_content=content,
            elevenlabs_doc_id=elevenlabs_doc_id,
            elevenlabs_doc_name=elevenlabs_doc_name
        )
        content_list = []
        content_list.append({
                "file_path": file_path,
                "text_content": content
            })

        # Local vector store creation removed - using ElevenLabs knowledge base directly
        # splits = get_splits(content_list)
        # vector_id = str(uuid.uuid4())
        # if splits:
        #     status, vector_path =convert_to_vectorstore(splits, vector_id)
        #     KnowledgeBaseModel.update(knowledge_base.id, vector_path=vector_path, vector_id=vector_id)
        
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


# =====================================================
# CALL HISTORY ENDPOINTS
# =====================================================

# DISABLED: Problematic duplicate endpoint that causes foreign key constraint errors
# @ElevenLabsAPIRouter.delete("/delete_audio_recording", name="delete_audio_recording")
# async def delete_audio_recording(request: Request):
#     """Delete an audio recording"""
#     try:
#         audio_recording_id = request.query_params.get("audio_recording_id")
#         if not audio_recording_id:
#             return JSONResponse(
#                 status_code=400, 
#                 content={"status": "error", "message": "Audio recording ID is required"}
#             )
#         
#         # Get the audio recording to check ownership
#         recording = AudioRecordings.get_by_id(audio_recording_id)
#         if not recording:
#             return JSONResponse(
#                 status_code=404,
#                 content={"status": "error", "message": "Audio recording not found"}
#             )
#         
#         # Check if user has access to this recording through agent ownership
#         agent = AgentModel.get_by_id(recording.agent_id)
#         user = request.session.get("user")
#         if not user or (agent and agent.created_by != user.get("user_id")):
#             return JSONResponse(
#                 status_code=403,
#                 content={"status": "error", "message": "Access denied"}
#             )
#         
#         # Delete the file if it exists
#         if recording.audio_file and os.path.exists(recording.audio_file):
#             try:
#                 os.remove(recording.audio_file)
#             except Exception as e:
#                 logger.warning(f"Could not delete audio file {recording.audio_file}: {str(e)}")
#         
#         # Delete the database record
#         AudioRecordings.delete(audio_recording_id)
#         
#         return JSONResponse(
#             status_code=200, 
#             content={"status": "success", "message": "Audio recording deleted successfully"}
#         )
#     
#     except Exception as e:
#         logger.error(f"Error deleting audio recording: {str(e)}")
#         return JSONResponse(
#             status_code=500, 
#             content={"status": "error", "message": "Something went wrong!", "error": str(e)}
#         )



@ElevenLabsAPIRouter.get("/debug/call_data")
async def debug_call_data(request: Request):
    """Debug endpoint to check call data structure"""
    try:
        user = request.session.get("user")
        if not user:
            return JSONResponse(status_code=401, content={"error": "Not authenticated"})
        
        with db():
            # Get sample data
            recordings = db.session.query(AudioRecordings).limit(3).all()
            calls = db.session.query(CallModel).limit(3).all()
            conversations = db.session.query(ConversationModel).limit(3).all()
            
            debug_data = {
                "audio_recordings": [
                    {
                        "id": r.id,
                        "call_id": r.call_id,
                        "audio_file": r.audio_file,
                        "created_at": str(r.created_at)
                    } for r in recordings
                ],
                "call_models": [
                    {
                        "id": c.id,
                        "call_id": c.call_id,
                        "variables": c.variables,
                        "variables_type": str(type(c.variables))
                    } for c in calls
                ],
                "conversations": [
                    {
                        "id": conv.id,
                        "audio_recording_id": conv.audio_recording_id,
                        "transcript_type": str(type(conv.transcript)),
                        "transcript_length": len(conv.transcript) if conv.transcript else 0,
                        "first_transcript_item": conv.transcript[0] if conv.transcript and len(conv.transcript) > 0 else None,
                        "summary": conv.summary
                    } for conv in conversations
                ]
            }
            
            return JSONResponse(status_code=200, content=debug_data)
            
    except Exception as e:
        logger.error(f"Debug endpoint error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@ElevenLabsAPIRouter.post("/call_details", name="call_details")
async def call_details(request: Request):
    """Get detailed information about a specific call"""
    try:
        transcript, summary = None, None
        data = await request.json()
        call_id = data.get("call_id")
        
        if not call_id:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Call ID is required"}
            )
        
        # Get the audio recording
        call = AudioRecordings.get_by_id(call_id)
        if not call:
            return JSONResponse(
                status_code=404,
                content={"status": "error", "message": "Call not found"}
            )
        
        # Check if user has access to this call through agent ownership
        agent = AgentModel.get_by_id(call.agent_id)
        user = request.session.get("user")
        if not user or (agent and agent.created_by != user.get("user_id")):
            return JSONResponse(
                status_code=403,
                content={"status": "error", "message": "Access denied"}
            )
        
        # Get conversation transcript
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
            
            # Get or generate summary
            if conversation.summary:
                summary = conversation.summary
            else:
                # Generate summary if not available
                try:
                    from app.utils.helper import generate_summary
                    summary = generate_summary(transcript)
                    ConversationModel.update_summary(conversation.id, summary)
                except ImportError:
                    summary = "Summary not available"
        
        # Get call details from CallModel
        call_details = CallModel.get_by_call_id(call.call_id) if hasattr(call, 'call_id') else None
        
        # Get dynamic variables and show only essential call information
        dynamic_variables = call_details.variables if call_details else (agent.dynamic_variable if agent else {})
        filtered_variables = {}
        
        if dynamic_variables and isinstance(dynamic_variables, dict):
            # Only show specific fields: start time, end time, conversation ID
            for key, value in dynamic_variables.items():
                if key == 'elevenlabs_conversation_id':
                    filtered_variables['Conversation ID'] = value
                elif key == 'created_timestamp':
                    filtered_variables['Start Time'] = value
                elif key == 'updated_timestamp':
                    filtered_variables['End Time'] = value
                elif 'start' in key.lower() and ('time' in key.lower() or 'timestamp' in key.lower()):
                    filtered_variables[key.replace('_', ' ').title()] = value
                elif 'end' in key.lower() and ('time' in key.lower() or 'timestamp' in key.lower()):
                    filtered_variables[key.replace('_', ' ').title()] = value

        return JSONResponse(
            status_code=200, 
            content={
                "status": "success", 
                "message": "Call details fetched successfully",
                "call": {
                    "id": call.id,
                    "internal_call_id": call_details.id if call_details else None,
                    "audio_file": call.audio_file,
                    "created_at": str(call.created_at) if hasattr(call, 'created_at') else None,
                    "agent_name": agent.agent_name if agent else "Unknown",
                    "duration": getattr(call, 'duration', 0) or 0
                },
                "transcript": transcript,
                "summary": summary,
                "dynamic_variable": filtered_variables
            }
        )
    
    except Exception as e:
        logger.error(f"Error getting call details: {str(e)}")
        return JSONResponse(
            status_code=500, 
            content={"status": "error", "message": "Something went wrong!", "error": str(e)}
        )


# ===============================
# CALL HISTORY API ENDPOINTS 
# ===============================

@ElevenLabsAPIRouter.delete("/delete_audio_recording/")
async def delete_audio_recording(request: Request, audio_recording_id: int = None):
    """Delete an audio recording and associated data - ElevenLabs version"""
    try:
        # Get audio_recording_id from query params if not in path
        if audio_recording_id is None:
            audio_recording_id = request.query_params.get("audio_recording_id")
            if audio_recording_id:
                audio_recording_id = int(audio_recording_id)
        
        if not audio_recording_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Audio recording ID is required"})
        
        # Get current user
        user = request.session.get("user")
        if not user:
            return JSONResponse(status_code=401, content={"status": "error", "message": "User not authenticated"})
        
        user_id = user.get("user_id")
        
        with db():
            # Get audio recording with agent relationship
            audio_recording = (db.session.query(AudioRecordings)
                             .join(AgentModel, AudioRecordings.agent_id == AgentModel.id)
                             .filter(AudioRecordings.id == audio_recording_id)
                             .filter(AgentModel.created_by == user_id)
                             .first())
            
            if not audio_recording:
                return JSONResponse(status_code=404, content={"status": "error", "message": "Audio recording not found or access denied"})
            
            # Get the call_id and audio_file path before any deletions
            call_id = audio_recording.call_id
            audio_file_path = audio_recording.audio_file
            audio_recording_id_for_deletion = audio_recording.id
            
            try:
                # IMPORTANT: Delete ALL associated conversations first (to avoid foreign key constraint)
                # Use explicit SQL to ensure all conversations are deleted
                conversations = db.session.query(ConversationModel).filter(
                    ConversationModel.audio_recording_id == audio_recording.id
                ).all()
                
               
                
                # Delete each conversation individually
                for conversation in conversations:
                    
                    db.session.delete(conversation)
                
                # Commit conversation deletions first to ensure they're gone before deleting the recording
                db.session.flush()
                
                # Now delete the audio recording
               
                db.session.delete(audio_recording)
                
                # Delete the main call record if it exists
                if call_id:
                    call_record = CallModel.get_by_call_id(call_id)
                    if call_record:
                        
                        db.session.delete(call_record)
                    else:
                        logger.warning(f"⚠️ Call record not found for call_id: {call_id}")
                
                # Commit all database deletions in one transaction
                db.session.commit()
               
            except Exception as inner_e:
                db.session.rollback()
               
                raise inner_e
            
            # Delete audio file after successful database commit
            if audio_file_path and audio_file_path.strip():
                try:
                    import os
                    # Convert relative path to absolute path
                    if audio_file_path.startswith('/audio/'):
                        # Remove /audio/ prefix and construct full path
                        relative_path = audio_file_path[7:]  # Remove '/audio/' prefix
                        file_path = os.path.join("audio_storage", relative_path)
                    else:
                        file_path = audio_file_path
                    
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        
                    else:
                        logger.warning(f"⚠️ Audio file not found: {file_path}")
                except Exception as e:
                    logger.warning(f"⚠️ Could not delete audio file: {e}")
            
            return JSONResponse(status_code=200, content={"status": "success", "message": "Call history and audio deleted successfully"})
    
    except Exception as e:
        logger.error(f"Error deleting audio recording: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete audio recording"})


@ElevenLabsAPIRouter.post("/call_details")
async def call_details(request: Request):
    """Get call details including transcript and summary - ElevenLabs version"""
    try:
        data = await request.json()
        call_id = data.get("call_id")
        
        if not call_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Call ID is required"})
        
        # Get current user
        user = request.session.get("user")
        if not user:
            return JSONResponse(status_code=401, content={"status": "error", "message": "User not authenticated"})
        
        user_id = user.get("user_id")
        
        with db():
            # Get call details with related data
            call_query = (db.session.query(CallModel, AgentModel, AudioRecordings, ConversationModel)
                         .join(AgentModel, CallModel.agent_id == AgentModel.id)
                         .outerjoin(AudioRecordings, CallModel.call_id == AudioRecordings.call_id)
                         .outerjoin(ConversationModel, AudioRecordings.id == ConversationModel.audio_recording_id)
                         .filter(CallModel.call_id == call_id)
                         .filter(AgentModel.created_by == user_id)
                         .first())
            
            if not call_query:
                return JSONResponse(status_code=404, content={"status": "error", "message": "Call not found or access denied"})
            
            call, agent, audio_recording, conversation = call_query
            
            # Build call details response
            call_info = {
                "call_id": call.call_id,
                "agent_name": agent.agent_name,
                "created_at": call.created_at.strftime('%Y-%m-%d %H:%M:%S') if call.created_at else None,
                "duration": getattr(audio_recording, 'duration', 0) if audio_recording else 0,
                "status": "completed"
            }
            
            # Extract transcript messages if available
            transcript = []
            summary = None
            
            if conversation and conversation.transcript:
                try:
                    # Handle both string and list formats
                    if isinstance(conversation.transcript, str):
                        import json
                        transcript_data = json.loads(conversation.transcript)
                    else:
                        transcript_data = conversation.transcript
                    
                    # Convert to expected format
                    if isinstance(transcript_data, list):
                        for msg in transcript_data:
                            if isinstance(msg, dict):
                                transcript.append({
                                    "role": msg.get("role", "user"),
                                    "content": msg.get("content", ""),
                                    "time_in_call_secs": msg.get("time_in_call_secs")
                                })
                    
                    summary = conversation.summary
                    
                except Exception as transcript_error:
                    logger.warning(f"Error parsing transcript: {transcript_error}")
            
            response_data = {
                "status": "success",
                "call": call_info,
                "transcript": transcript,
                "summary": summary
            }
            
            return JSONResponse(status_code=200, content=response_data)
    
    except Exception as e:
        logger.error(f"Error fetching call details: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to fetch call details"})


@ElevenLabsAPIRouter.get("/call_history")
async def get_call_history(request: Request):
    """Get call history for the current user with pagination and filtering"""
    try:
        user = request.session.get("user")
        if not user:
            return JSONResponse(status_code=401, content={"status": "error", "message": "User not authenticated"})
        
        # Get query parameters
        page = int(request.query_params.get("page", 1))
        limit = min(int(request.query_params.get("limit", 20)), 100)  # Max 100 per page
        agent_id = request.query_params.get("agent_id")
        start_date = request.query_params.get("start_date")  # Format: YYYY-MM-DD
        end_date = request.query_params.get("end_date")      # Format: YYYY-MM-DD
        
        offset = (page - 1) * limit
        
        with db():
            # Base query - get calls for user's agents
            query = (db.session.query(CallModel, AgentModel, AudioRecordings, ConversationModel)
                    .join(AgentModel, CallModel.agent_id == AgentModel.id)
                    .outerjoin(AudioRecordings, CallModel.call_id == AudioRecordings.call_id)
                    .outerjoin(ConversationModel, AudioRecordings.id == ConversationModel.audio_recording_id)
                    .filter(AgentModel.created_by == user.get("user_id")))
            
            # Apply filters
            if agent_id:
                query = query.filter(CallModel.agent_id == int(agent_id))
            
            if start_date:
                from datetime import datetime
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                query = query.filter(CallModel.created_at >= start_dt)
            
            if end_date:
                from datetime import datetime
                end_dt = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
                query = query.filter(CallModel.created_at <= end_dt)
            
            # Get total count
            total_calls = query.count()
            
            # Get paginated results
            results = (query.order_by(CallModel.created_at.desc())
                      .offset(offset)
                      .limit(limit)
                      .all())
            
            call_history = []
            for call, agent, audio_recording, conversation in results:
                call_data = {
                    "call_id": call.call_id,
                    "agent_name": agent.agent_name,
                    "agent_id": agent.id,
                    "created_at": call.created_at.isoformat() if call.created_at else None,
                    "has_audio": audio_recording is not None,
                    "has_transcript": conversation is not None,
                    "duration": None,
                    "summary": conversation.summary if conversation else None
                }
                
                # If audio recording exists, get duration
                if audio_recording:
                    call_data["audio_name"] = audio_recording.audio_name
                    call_data["audio_file"] = audio_recording.audio_file
                
                call_history.append(call_data)
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "data": call_history,
                    "pagination": {
                        "page": page,
                        "limit": limit,
                        "total": total_calls,
                        "total_pages": (total_calls + limit - 1) // limit
                    }
                }
            )
    
    except Exception as e:
        logger.error(f"Error fetching call history: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to fetch call history"})


@ElevenLabsAPIRouter.get("/call_details/{call_id}")
async def get_call_details(call_id: str, request: Request):
    """Get detailed information about a specific call"""
    try:
        user = request.session.get("user")
        if not user:
            return JSONResponse(status_code=401, content={"status": "error", "message": "User not authenticated"})
        
        with db():
            # Get call with all related data
            result = (db.session.query(CallModel, AgentModel, AudioRecordings, ConversationModel)
                     .join(AgentModel, CallModel.agent_id == AgentModel.id)
                     .outerjoin(AudioRecordings, CallModel.call_id == AudioRecordings.call_id)
                     .outerjoin(ConversationModel, AudioRecordings.id == ConversationModel.audio_recording_id)
                     .filter(CallModel.call_id == call_id)
                     .filter(AgentModel.created_by == user.get("user_id"))
                     .first())
            
            if not result:
                return JSONResponse(status_code=404, content={"status": "error", "message": "Call not found"})
            
            call, agent, audio_recording, conversation = result
            
            call_details = {
                "call_id": call.call_id,
                "agent": {
                    "id": agent.id,
                    "name": agent.agent_name,
                    "phone_number": agent.phone_number,
                    "voice_id": agent.selected_voice
                },
                "created_at": call.created_at.isoformat() if call.created_at else None,
                "elevenlabs_conversation_id": call.variables.get("elevenlabs_conversation_id") if call.variables else None,
                "audio_recording": None,
                "conversation": None
            }
            
            # Add audio recording details
            if audio_recording:
                call_details["audio_recording"] = {
                    "id": audio_recording.id,
                    "name": audio_recording.audio_name,
                    "file": audio_recording.audio_file,
                    "created_at": audio_recording.created_at.isoformat() if audio_recording.created_at else None
                }
            
            # Add conversation details
            if conversation:
                call_details["conversation"] = {
                    "id": conversation.id,
                    "transcript": conversation.transcript,
                    "summary": conversation.summary,
                    "created_at": conversation.created_at.isoformat() if conversation.created_at else None
                }
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "data": call_details
                }
            )
    
    except Exception as e:
        logger.error(f"Error fetching call details: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to fetch call details"})


@ElevenLabsAPIRouter.get("/call_analytics")
async def get_call_analytics(request: Request):
    """Get call analytics and statistics for the current user"""
    try:
        user = request.session.get("user")
        if not user:
            return JSONResponse(status_code=401, content={"status": "error", "message": "User not authenticated"})
        
        # Get date range from query params
        days = int(request.query_params.get("days", 30))  # Default last 30 days
        agent_id = request.query_params.get("agent_id")
        
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        with db():
            # Base query for user's calls
            base_query = (db.session.query(CallModel)
                         .join(AgentModel, CallModel.agent_id == AgentModel.id)
                         .filter(AgentModel.created_by == user.get("user_id"))
                         .filter(CallModel.created_at >= start_date)
                         .filter(CallModel.created_at <= end_date))
            
            if agent_id:
                base_query = base_query.filter(CallModel.agent_id == int(agent_id))
            
            # Total calls
            total_calls = base_query.count()
            
            # Calls with audio recordings
            calls_with_audio = (base_query
                               .join(AudioRecordings, CallModel.call_id == AudioRecordings.call_id)
                               .count())
            
            # Calls with transcripts
            calls_with_transcripts = (base_query
                                    .join(AudioRecordings, CallModel.call_id == AudioRecordings.call_id)
                                    .join(ConversationModel, AudioRecordings.id == ConversationModel.audio_recording_id)
                                    .count())
            
            # Calls by agent
            agent_stats = (db.session.query(AgentModel.agent_name, AgentModel.id, func.count(CallModel.id).label('call_count'))
                          .join(CallModel, AgentModel.id == CallModel.agent_id)
                          .filter(AgentModel.created_by == user.get("user_id"))
                          .filter(CallModel.created_at >= start_date)
                          .filter(CallModel.created_at <= end_date))
            
            if agent_id:
                agent_stats = agent_stats.filter(AgentModel.id == int(agent_id))
            
            agent_stats = agent_stats.group_by(AgentModel.id, AgentModel.agent_name).all()
            
            # Daily call counts for the chart
            daily_calls = (db.session.query(
                              func.date(CallModel.created_at).label('call_date'),
                              func.count(CallModel.id).label('call_count')
                          )
                          .join(AgentModel, CallModel.agent_id == AgentModel.id)
                          .filter(AgentModel.created_by == user.get("user_id"))
                          .filter(CallModel.created_at >= start_date)
                          .filter(CallModel.created_at <= end_date))
            
            if agent_id:
                daily_calls = daily_calls.filter(CallModel.agent_id == int(agent_id))
            
            daily_calls = daily_calls.group_by(func.date(CallModel.created_at)).order_by(func.date(CallModel.created_at)).all()
            
            analytics_data = {
                "summary": {
                    "total_calls": total_calls,
                    "calls_with_audio": calls_with_audio,
                    "calls_with_transcripts": calls_with_transcripts,
                    "audio_completion_rate": round((calls_with_audio / total_calls * 100), 2) if total_calls > 0 else 0,
                    "transcript_completion_rate": round((calls_with_transcripts / total_calls * 100), 2) if total_calls > 0 else 0,
                    "date_range": {
                        "start": start_date.isoformat(),
                        "end": end_date.isoformat(),
                        "days": days
                    }
                },
                "agent_breakdown": [
                    {
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "call_count": call_count
                    }
                    for agent_name, agent_id, call_count in agent_stats
                ],
                "daily_calls": [
                    {
                        "date": call_date.isoformat(),
                        "count": call_count
                    }
                    for call_date, call_count in daily_calls
                ]
            }
            
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "data": analytics_data
                }
            )
    
    except Exception as e:
        logger.error(f"Error fetching call analytics: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to fetch call analytics"})


@ElevenLabsAPIRouter.delete("/call_history/{call_id}")
async def delete_call_record(call_id: str, request: Request):
    """Delete a call record and its associated data"""
    try:
        user = request.session.get("user")
        if not user:
            return JSONResponse(status_code=401, content={"status": "error", "message": "User not authenticated"})
        
        with db():
            # Find the call and verify ownership
            call_result = (db.session.query(CallModel, AgentModel)
                          .join(AgentModel, CallModel.agent_id == AgentModel.id)
                          .filter(CallModel.call_id == call_id)
                          .filter(AgentModel.created_by == user.get("user_id"))
                          .first())
            
            if not call_result:
                return JSONResponse(status_code=404, content={"status": "error", "message": "Call not found"})
            
            call, agent = call_result
            
            # Delete related audio recording and conversation
            audio_recording = AudioRecordings.get_by_call_id(call_id)
            if audio_recording:
                # Delete conversation if exists
                conversation = ConversationModel.get_by_audio_recording_id(audio_recording.id)
                if conversation:
                    ConversationModel.delete(conversation.id)
                
                # Delete audio file from filesystem
                if audio_recording.audio_file and os.path.exists(audio_recording.audio_file):
                    try:
                        os.remove(audio_recording.audio_file)
                    except Exception as e:
                        logger.warning(f"Could not delete audio file: {e}")
                
                # Delete audio recording from database
                db.session.delete(audio_recording)
            
            # Delete the call record
            db.session.delete(call)
            db.session.commit()
            
            return JSONResponse(
                status_code=200,
                content={"status": "success", "message": "Call record deleted successfully"}
            )
    
    except Exception as e:
        logger.error(f"Error deleting call record: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete call record"})


@ElevenLabsAPIRouter.get("/get_agent_connection", name="get_agent_connection")
async def get_agent_connection(request: Request):
    try:
        agent_id = request.query_params.get("agent_id")
        if not agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Agent ID is required"})
        
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