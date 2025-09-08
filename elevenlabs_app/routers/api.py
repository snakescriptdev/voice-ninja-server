import os
import re
import shutil
import uuid
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
from app.utils.langchain_integration import get_splits, convert_to_vectorstore




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

@ElevenLabsAPIRouter.delete("/delete_agent",name='delete-agent')
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
                    print(f"🔍 Debug: Updated {field} to: {value}")
            
            # Update the updated_at timestamp
            current_agent.updated_at = func.now()
            
            print(f"🔍 Debug: About to commit changes to database...")
            db.session.commit()
            print(f"🔍 Debug: Database commit successful!")
            
            # Refresh the object to ensure we have latest values
            db.session.refresh(current_agent)
            print(f"🔍 Debug: After refresh - agent_prompt: {current_agent.agent_prompt}")
            print(f"🔍 Debug: After refresh - selected_language: {current_agent.selected_language}")
            
            # Verify by direct database query
            from sqlalchemy import text
            result = db.session.execute(
                text("SELECT agent_prompt, selected_language, welcome_msg FROM agents WHERE id = :agent_id"), 
                {"agent_id": agent_id}
            )
            row = result.fetchone()
            print(f"🔍 Debug: Direct DB query - agent_prompt: {row[0] if row else 'None'}, selected_language: {row[1] if row else 'None'}, welcome_msg: {row[2] if row else 'None'}")
            
            return True, None, current_agent
            
    except Exception as e:
        print(f"🔍 Debug: Database update error: {str(e)}")
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

        # Validate language selection
        if selected_language_code:
            selected_model_rec = ElevenLabModel.get_by_name(DEFAULT_MODEL_ELEVENLAB)
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
                print(f"🔍 Debug: Dynamic variables found in prompt: {list(new_variables)}")
                
                # Get existing dynamic variables if any
                existing_variables = agent_rec.dynamic_variable if hasattr(agent_rec, 'dynamic_variable') else {}
                print(f"🔍 Debug: Existing dynamic variables: {existing_variables}")
                
                # Merge existing and new variables
                merged_variables = {**existing_variables, **{var: "" for var in new_variables if var not in existing_variables}}
                print(f"🔍 Debug: Merged dynamic variables: {merged_variables}")
                
                # Add dynamic variables to update data
                if merged_variables:
                    update_data['dynamic_variable'] = merged_variables
                    print(f"🔍 Debug: Added dynamic_variable to update data")
                
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

        print(f"🔍 Debug: Fields to update: {list(update_data.keys())}")
        if 'welcome_msg' in update_data:
            print(f"🔍 Debug: Welcome message to update: {update_data['welcome_msg']}")
        if 'dynamic_variable' in update_data:
            print(f"🔍 Debug: Dynamic variables to update: {update_data['dynamic_variable']}")
        
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
            print(f"🔍 Debug: About to call ElevenLabs update_agent:")
            print(f"  - agent_id: {agent_rec.elvn_lab_agent_id}")
            print(f"  - name: {agent_name if agent_name else None}")
            print(f"  - prompt: {prompt if prompt else None}")
            print(f"  - model: {selected_llm_model_rec.name if selected_llm_model_rec else None}")
            print(f"  - voice_id: {elevenlabs_voice_id if elevenlabs_voice_id else None}")
            print(f"  - language: {selected_language_code if selected_language_code else None}")
            print(f"  - selected_elevenlab_model: {DEFAULT_MODEL_ELEVENLAB if DEFAULT_MODEL_ELEVENLAB else None}")
            print(f"  - first_message: {welcome_msg if welcome_msg else None}")
            if welcome_msg:
                print(f"🔍 Debug: ElevenLabs will receive welcome message: {welcome_msg}")
            
            # Get dynamic variables from update data if available
            dynamic_vars = update_data.get('dynamic_variable')
            if dynamic_vars:
                print(f"🔍 Debug: ElevenLabs will receive dynamic variables: {dynamic_vars}")
            
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
                    print(f"🔍 Debug: Saving dynamic variables to local database: {update_data['dynamic_variable']}")
                    AgentModel.update_dynamic_variables(agent_id, update_data['dynamic_variable'])
                    print(f"✅ Success: Dynamic variables saved to local database")
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
                print(f"🔍 Debug: Updating agent {agent.elvn_lab_agent_id} with new variables")
                
                # Get the current prompt from the agent record
                current_prompt = agent.agent_prompt or ""
                print(f"🔍 Debug: Current prompt: {current_prompt}")
                
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
                    print(f"🔍 Debug: Updated prompt with variables: {updated_prompt}")
                    
                    # Update the agent with the new prompt (mimicking edit agent approach)
                    update_result = ElevenLabsAgentCRUD().update_agent(
                        agent_id=agent.elvn_lab_agent_id,
                        prompt=updated_prompt,
                        dynamic_variables=variables
                    )
                    
                    if "error" in update_result:
                        print(f"❌ Error: Failed to update agent: {update_result}")
                        return JSONResponse(status_code=500, content={
                            "status": "error", 
                            "message": f"Failed to update ElevenLabs agent: {update_result.get('exc', 'Unknown error')}"
                        })
                    else:
                        print(f"✅ Success: Agent updated with new prompt and variables")
                        
                        # Also update the local agent record with the new prompt
                        try:
                            # Update the agent_prompt field in local database
                            from app.databases.models import AgentModel as LocalAgentModel
                            
                            # Use the proper update_prompt method
                            LocalAgentModel.update_prompt(agent_id, updated_prompt)
                            
                            print(f"✅ Success: Local agent prompt updated with variables")
                        except Exception as local_update_error:
                            print(f"⚠️ Warning: Failed to update local agent prompt: {str(local_update_error)}")
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
        print(f"🔍 Debug: Uploading file to ElevenLabs: {file.filename}")
        file_info = ElevenLabsAgentCRUD().upload_file_to_knowledge_base(file_path, name=file.filename)
        print(f"🔍 Debug: ElevenLabs response: {file_info}")
        
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
            
            print(f"🔍 Debug: Found {len(agent_relations)} agents using this knowledge base")
            
            if agent_relations:
                # Process each agent
                for agent_relation in agent_relations:
                    agent_id = agent_relation.agent_id
                    agent = AgentModel.get_by_id(agent_id)
                    
                    if agent and hasattr(agent, 'elvn_lab_agent_id') and agent.elvn_lab_agent_id:
                        print(f"🔍 Debug: Updating agent {agent.elvn_lab_agent_id} with new file")
                        
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
                                print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} has {len(existing_kb_files)} existing knowledge base files")
                            
                            # Add the new file to existing files
                            combined_kb_files = existing_kb_files + [new_file_data]
                            print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} combined knowledge base files: {combined_kb_files}")
                            
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
        print(f"🔍 Debug: Deleting file - file_id: {file_id}, knowledge_base_id: {knowledge_base_id}, elevenlabs_doc_id: {elevenlabs_doc_id}")
        
        file = KnowledgeBaseFileModel.get_by_id(file_id)
        if file:
            if file.knowledge_base_id == knowledge_base_id:
                print(f"🔍 Debug: Found file in database - file_id: {file_id}, knowledge_base_id: {knowledge_base_id}")
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
                    
                    print(f"🔍 Debug: Found {len(agent_relations)} agents using this knowledge base")
                    
                    if agent_relations:
                        # Process each agent
                        for agent_relation in agent_relations:
                            agent_id = agent_relation.agent_id
                            agent = AgentModel.get_by_id(agent_id)
                            
                            if agent and hasattr(agent, 'elvn_lab_agent_id') and agent.elvn_lab_agent_id:
                                print(f"🔍 Debug: Removing file from agent {agent.elvn_lab_agent_id}")
                                
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
                                        print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} has {len(existing_kb_files)} existing knowledge base files")
                                    
                                    # Remove the file we're deleting
                                    updated_kb_files = [kb_file for kb_file in existing_kb_files if kb_file.get("id") != elevenlabs_doc_id]
                                    print(f"🔍 Debug: Agent {agent.elvn_lab_agent_id} updated KB files (removed {elevenlabs_doc_id}): {updated_kb_files}")
                                    
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
                print(f"🔍 Debug: Attempting to delete file from ElevenLabs with doc_id: {elevenlabs_doc_id}")
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
                    print(f"🔍 Debug: Deleting file from local storage with file_id: {file_id}")
                    KnowledgeBaseFileModel.delete(file_id)
                    print(f"✅ Success: File deleted from local storage successfully")
                except Exception as local_delete_error:
                    # If local deletion fails, log the error but don't fail the entire operation
                    # since ElevenLabs deletion was successful
                    print(f"⚠️ Warning: Failed to delete file from local storage: {str(local_delete_error)}")
                
                # If this was the last file, delete the knowledge base too
                if len(files) == 1:  # Only had 1 file which we just deleted
                    try:
                        print(f"🔍 Debug: Deleting knowledge base with id: {knowledge_base_id}")
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
            agent = AgentModel.get_by_id(agent_id)
            if agent:
                # Step 1: Get ElevenLabs Knowledge Base IDs for this specific knowledge base
                try:
                    knowledge_base_files = KnowledgeBaseFileModel.get_all_by_knowledge_base(knowledge_base_id)
                    
                    # Extract ElevenLabs doc IDs, filtering out any None/empty values
                    elevenlabs_kb_ids = []
                    for file in knowledge_base_files:
                        if file.elevenlabs_doc_id and file.elevenlabs_doc_id.strip():
                            elevenlabs_kb_ids.append(file.elevenlabs_doc_id)
                            print(f"🔍 Debug: Found file '{file.file_name}' with ElevenLabs doc_id: {file.elevenlabs_doc_id}")
                    
                    print(f"🔍 Debug: Total files found for KB {knowledge_base_id}: {len(knowledge_base_files)}")
                    print(f"🔍 Debug: Valid ElevenLabs doc_ids: {len(elevenlabs_kb_ids)}")
                    print(f"🔍 Debug: ElevenLabs doc_ids: {elevenlabs_kb_ids}")
                    
                    if not elevenlabs_kb_ids:
                        return JSONResponse(status_code=400, content={
                            "status": "error", 
                            "message": f"No valid ElevenLabs files found in knowledge base {knowledge_base_id}"
                        })
                    
                    print(f"🔍 Debug: Will attach {len(elevenlabs_kb_ids)} files to agent {agent_id}")
                    
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
                        
                        print(f"🔍 Debug: Formatted knowledge base data for ElevenLabs: {knowledge_base_data}")
                        
                        elevenlabs_result = ElevenLabsAgentCRUD().update_agent(
                            agent_id=agent.elvn_lab_agent_id,
                            knowledge_base=knowledge_base_data
                        )
                        
                        if elevenlabs_result.get("error"):
                            print(f"❌ Error: Failed to update ElevenLabs agent: {elevenlabs_result.get('exc')}")
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
                        print(f"🔍 Debug: Removed old knowledge base association for agent {agent_id}")

                    if knowledge_base_id:
                        # If no association exists, insert a new one
                        if not existing_association or existing_association.knowledge_base_id != knowledge_base_id:
                            stmt = insert(agent_knowledge_association).values(
                                agent_id=agent_id, 
                                knowledge_base_id=knowledge_base_id
                            )
                            session.execute(stmt)
                            session.commit()
                            print(f"🔍 Debug: Created new knowledge base association for agent {agent_id}")

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
        print(f"🔍 Debug: Local agent ID: {agent_id}")
        print(f"🔍 Debug: ElevenLabs agent ID: '{eleven_agent_id}' (type: {type(eleven_agent_id)})")
        
        if not eleven_agent_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "ElevenLabs agent ID is required. Please ensure the agent is properly created in ElevenLabs."})
        
        # Validate required fields
        tool_name = form_data.get("tool_name", "")
        tool_description = form_data.get("tool_description", "")
        
        print(f"🔍 Debug: Received form_data: {form_data}")
        print(f"🔍 Debug: tool_name: '{tool_name}' (type: {type(tool_name)})")
        print(f"🔍 Debug: tool_description: '{tool_description}' (type: {type(tool_description)})")
        
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
        
        # Check if tool name already exists in ElevenLabs for this agent
        try:
            elevenlabs_tools = ElevenLabsAgentCRUD().get_agent_tools(eleven_agent_id)
            if "error" not in elevenlabs_tools:
                existing_elevenlabs_tools = elevenlabs_tools.get("tools", [])
                for tool in existing_elevenlabs_tools:
                    if tool.get("name") == tool_name:
                        return JSONResponse(status_code=400, content={
                            "status": "error", 
                            "message": f"A webhook tool with the name '{tool_name}' already exists in ElevenLabs for this agent."
                        })
        except Exception as e:
            print(f"⚠️ Warning: Could not check existing ElevenLabs tools: {e}")
            # Continue with creation - this is not a critical error
        
        # Build complete ElevenLabs tool_config structure
        tool_config = build_elevenlabs_tool_config(form_data)
        
        print(f"🔍 Debug: Built ElevenLabs tool_config: {json.dumps(tool_config, indent=2)}")
        
        # Check if request_body_schema is properly set for POST requests
        api_schema = tool_config.get("tool_config", {}).get("api_schema", {})
        http_method = api_schema.get("method", "")
        request_body_schema = api_schema.get("request_body_schema")
        
        print(f"🔍 Debug: HTTP method: '{http_method}'")
        print(f"🔍 Debug: Request body schema: {request_body_schema}")
        
        if http_method in ["POST", "PUT", "PATCH"] and request_body_schema is None:
            print("🚨 Error: POST/PUT/PATCH method requires request_body_schema but got None")
            return JSONResponse(status_code=400, content={
                "status": "error", 
                "message": "POST/PUT/PATCH methods require a request body schema. Please add request body properties."
            })
        
        # Step 1: Create the tool in ElevenLabs (without agent_id)
        result = ElevenLabsAgentCRUD().create_webhook_function(tool_config)
        
        if "error" in result:
            return JSONResponse(status_code=500, content={
                "status": "error", 
                "message": f"Failed to create webhook function: {result.get('exc', 'Unknown error')}"
            })
        
        # Extract ElevenLabs tool ID from result
        elevenlabs_tool_id = None
        if isinstance(result, dict) and "id" in result:
            elevenlabs_tool_id = result["id"]
        elif isinstance(result, dict) and "tool_id" in result:
            elevenlabs_tool_id = result["tool_id"]
        
        print(f"🔍 Debug: Created tool in ElevenLabs with ID: {elevenlabs_tool_id}")
        
        if not elevenlabs_tool_id:
            return JSONResponse(status_code=500, content={
                "status": "error", 
                "message": "Failed to get tool ID from ElevenLabs response"
            })
        
        # Step 2: Get existing tools from agent's conversation config and add the new tool
        print(f"🔍 Debug: Getting existing tools for agent {eleven_agent_id}")
        existing_agent_result = ElevenLabsAgentCRUD().get_agent(eleven_agent_id)
        
        existing_tool_ids = []
        if "error" not in existing_agent_result:
            # Extract tool_ids from agent's conversation config
            conversation_config = existing_agent_result.get("conversation_config", {})
            agent_config = conversation_config.get("agent", {})
            prompt_config = agent_config.get("prompt", {})
            existing_tool_ids = prompt_config.get("tool_ids", [])
            print(f"🔍 Debug: Found {len(existing_tool_ids)} existing tools in agent config: {existing_tool_ids}")
        else:
            print(f"⚠️ Warning: Failed to get agent config: {existing_agent_result}")
        
        # Add the new tool to the existing tools list
        all_tool_ids = existing_tool_ids + [elevenlabs_tool_id]
        print(f"🔍 Debug: Updating agent {eleven_agent_id} with all tools: {all_tool_ids}")
        
        update_result = ElevenLabsAgentCRUD().update_agent_tools(eleven_agent_id, all_tool_ids)
        
        if "error" in update_result:
            # If agent update fails, we should clean up the created tool
            print(f"⚠️ Warning: Failed to attach tool to agent: {update_result.get('exc')}")
            # Note: We could add tool deletion here, but for now we'll continue
            # The tool exists in ElevenLabs but isn't attached to the agent
        
        # Save to local database
        try:
            local_tool = ElevenLabsWebhookToolModel.create(
                agent_id=agent_id,
                tool_name=tool_name,
                tool_description=tool_description,
                tool_config=tool_config,
                elevenlabs_tool_id=elevenlabs_tool_id
            )
            
            print(f"✅ Success: Saved webhook tool to local database with ID: {local_tool.id}")
            
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
            print(f"❌ Error: Failed to save to local database: {str(db_error)}")
            # Even if local save fails, we still return success since ElevenLabs creation succeeded
            return JSONResponse(status_code=200, content={
                "status": "success", 
                "message": "Webhook tool created in ElevenLabs successfully (local save failed)",
                "data": result
            })
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})

def build_elevenlabs_tool_config(form_data):
    """
    Build the complete ElevenLabs tool_config structure from form data.
    Handles all nested fields properly according to the WebhookToolConfig schema.
    """
    
    print(f"🔍 Debug: build_elevenlabs_tool_config received: {form_data}")
    
    # Extract basic fields with None safety
    tool_name = form_data.get("tool_name") or ""
    tool_description = form_data.get("tool_description") or ""
    api_url = form_data.get("api_url") or ""
    http_method = form_data.get("http_method") or "POST"
    try:
        response_timeout = int(form_data.get("response_timeout") or 20)
    except (ValueError, TypeError):
        response_timeout = 20
    body_description = form_data.get("body_description") or ""
    
    print(f"🔍 Debug: Extracted fields - tool_name: '{tool_name}', api_url: '{api_url}'")
    
    # Extract nested arrays from form data
    path_params = form_data.get("path_params", [])
    query_params = form_data.get("query_params", [])
    request_body_properties = form_data.get("request_body_properties", [])
    request_headers = form_data.get("request_headers", [])
    dynamic_variables = form_data.get("dynamic_variables", [])
    assignments = form_data.get("assignments", [])
    
    # Build path_params_schema (ElevenLabs expects flat dictionary, not JSON Schema)
    path_params_schema = {}
    
    # First, extract all placeholders from the URL
    import re
    url_placeholders = re.findall(r'\{([^}]+)\}', api_url)
    print(f"🔍 Debug: Found URL placeholders: {url_placeholders}")
    
    # Add placeholders from URL to path_params_schema with default values
    for placeholder in url_placeholders:
        if placeholder not in path_params_schema:
            path_params_schema[placeholder] = {
                "type": "string",
                "description": f"Path parameter {placeholder}"
            }
            print(f"🔍 Debug: Added default path parameter: {placeholder}")
    
    # Then process explicitly defined path parameters
    if path_params:
        for param in path_params:
            param_name = param.get("name", "")
            param_type = param.get("type", "string")
            param_description = param.get("description", "")
            param_required = param.get("required", False)
            param_dynamic_var = param.get("dynamic_variable", "")
            param_constant_value = param.get("constant_value", "")
            
            if param_name:
                # Check if the URL contains a placeholder for this parameter
                placeholder = f"{{{param_name}}}"
                if placeholder in api_url:
                    # Create the property object with type
                    property_obj = {"type": param_type}
                    
                    # Add the appropriate value field based on what's provided
                    if param_description:
                        property_obj["description"] = param_description
                    elif param_dynamic_var:
                        property_obj["dynamic_variable"] = param_dynamic_var
                    elif param_constant_value:
                        property_obj["constant_value"] = param_constant_value
                    
                    # Update the existing entry or create new one
                    path_params_schema[param_name] = property_obj
                    print(f"🔍 Debug: Updated path parameter: {param_name}")
                else:
                    print(f"⚠️ Warning: Path parameter '{param_name}' defined but URL doesn't contain placeholder '{placeholder}'. Skipping this parameter.")
    
    print(f"🔍 Debug: Final path_params_schema: {path_params_schema}")
    
    # Build query_params_schema
    query_params_schema = None
    if query_params:
        query_params_schema = {
            "type": "object",
            "properties": {},
            "required": []
        }
        for param in query_params:
            param_name = param.get("name", "")
            param_type = param.get("type", "string")
            param_description = param.get("description", "")
            param_required = param.get("required", False)
            param_dynamic_var = param.get("dynamic_variable", "")
            param_constant_value = param.get("constant_value", "")
            
            if param_name:
                # Create the property object with type
                property_obj = {"type": param_type}
                
                # Add the appropriate value field based on what's provided
                if param_description:
                    property_obj["description"] = param_description
                elif param_dynamic_var:
                    property_obj["dynamic_variable"] = param_dynamic_var
                elif param_constant_value:
                    property_obj["constant_value"] = param_constant_value
                
                query_params_schema["properties"][param_name] = property_obj
                
                if param_required:
                    query_params_schema["required"].append(param_name)
    
    # Build request_body_schema (only for POST/PUT/PATCH methods)
    request_body_schema = None
    if http_method in ["POST", "PUT", "PATCH"]:
        # ElevenLabs expects a simple object schema for request body
        request_body_schema = {
            "type": "object",
            "description": body_description or "",
            "properties": {},
            "required": []
        }
        
        # Add properties if any are defined
        for prop in request_body_properties:
            prop_name = prop.get("name", "")
            prop_type = prop.get("type", "string")
            prop_description = prop.get("description", "")
            prop_required = prop.get("required", False)
            prop_dynamic_var = prop.get("dynamic_variable", "")
            prop_constant_value = prop.get("constant_value", "")
            
            if prop_name:
                # Create property object matching ElevenLabs format
                property_obj = {
                    "type": prop_type,
                    "description": prop_description
                }
                
                # Add dynamic variable or constant value if provided
                if prop_dynamic_var:
                    property_obj["dynamic_variable"] = prop_dynamic_var
                elif prop_constant_value:
                    property_obj["constant_value"] = prop_constant_value
                
                request_body_schema["properties"][prop_name] = property_obj
                
                # Add to required list if marked as required
                if prop_required:
                    request_body_schema["required"].append(prop_name)
    
    # Build request_headers
    request_headers_dict = {}
    for header in request_headers:
        header_name = header.get("name", "")
        header_value = header.get("value", "")
        header_type = header.get("type", "string")  # "string", "secret", "dynamic_variable"
        
        if header_name and header_value:
            if header_type == "secret":
                request_headers_dict[header_name] = {
                    "type": "secret",
                    "secret_id": header_value
                }
            elif header_type == "dynamic_variable":
                request_headers_dict[header_name] = {
                    "variable_name": header_value
                }
            else:
                request_headers_dict[header_name] = header_value
    
    # Build dynamic_variables
    dynamic_variable_placeholders = {}
    for var in dynamic_variables:
        var_name = var.get("name", "")
        var_value = var.get("value", "")
        if var_name:
            # Try to parse as number/boolean, otherwise keep as string
            try:
                if var_value.lower() in ['true', 'false']:
                    dynamic_variable_placeholders[var_name] = var_value.lower() == 'true'
                elif '.' in var_value:
                    dynamic_variable_placeholders[var_name] = float(var_value)
                else:
                    dynamic_variable_placeholders[var_name] = int(var_value)
            except (ValueError, AttributeError):
                dynamic_variable_placeholders[var_name] = var_value
    
    # Build assignments
    assignments_list = []
    for assignment in assignments:
        dynamic_var = assignment.get("dynamic_variable", "")
        value_path = assignment.get("value_path", "")
        source = assignment.get("source", "response")
        
        if dynamic_var and value_path:
            assignments_list.append({
                "dynamic_variable": dynamic_var,
                "value_path": value_path,
                "source": source
            })
    
    # Build the complete tool_config
    tool_config = {
        "tool_config": {
            "type": "webhook",
            "name": tool_name,
            "description": tool_description,
            "response_timeout_secs": response_timeout,
            "disable_interruptions": form_data.get("disable_interruptions", False),
            "force_pre_tool_speech": form_data.get("force_pre_tool_speech", False),
            "api_schema": {
                "url": api_url,
                "method": http_method,
                "path_params_schema": path_params_schema if path_params_schema else {},
                "query_params_schema": query_params_schema,
                "request_body_schema": request_body_schema,
                "request_headers": request_headers_dict if request_headers_dict else {},
                "auth_connection": None
            },
            "dynamic_variables": {
                "dynamic_variable_placeholders": dynamic_variable_placeholders
            },
            "assignments": assignments_list
        }
    }
    
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
        
        print(f"🔍 Debug: Deleting tool {function_id} with ElevenLabs ID: {tool.elevenlabs_tool_id}")
        
        # Get the agent to get the ElevenLabs agent ID
        agent = AgentModel.get_by_id(tool.agent_id)
        if not agent or not agent.elvn_lab_agent_id:
            print(f"⚠️ Warning: Agent or ElevenLabs agent ID not found for agent {tool.agent_id}")
        else:
            # Step 1: Remove tool from agent's tool list in ElevenLabs
            try:
                existing_tools_result = ElevenLabsAgentCRUD().get_agent_tools(agent.elvn_lab_agent_id)
                if "error" not in existing_tools_result:
                    existing_tools = existing_tools_result.get("tools", [])
                    existing_tool_ids = [t.get("id") for t in existing_tools if t.get("id")]
                    
                    # Remove the tool ID from the list
                    updated_tool_ids = [tid for tid in existing_tool_ids if tid != tool.elevenlabs_tool_id]
                    
                    print(f"🔍 Debug: Updating agent {agent.elvn_lab_agent_id} with tools: {updated_tool_ids}")
                    
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
            print(f"🔍 Debug: get_custom_functions - Raw database values:")
            print(f"🔍 Debug: - function.tool_name: '{function.tool_name}'")
            print(f"🔍 Debug: - function.tool_description: '{function.tool_description}'")
            print(f"🔍 Debug: - function.tool_config type: {type(function.tool_config)}")
            print(f"🔍 Debug: - function.tool_config: {function.tool_config}")
            
            # Extract api_url and timeout from tool_config
            tool_config = function.tool_config or {}
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
            
            print(f"🔍 Debug: get_custom_functions returning data for function_id {function_id}:")
            print(f"🔍 Debug: - id: {function_data['id']}")
            print(f"🔍 Debug: - function_name: '{function_data['function_name']}'")
            print(f"🔍 Debug: - function_description: '{function_data['function_description']}'")
            print(f"🔍 Debug: - function_url: '{function_data['function_url']}'")
            print(f"🔍 Debug: - function_timeout: {function_data['function_timeout']}")
            print(f"🔍 Debug: - function_parameters keys: {list(function_data['function_parameters'].keys()) if function_data['function_parameters'] else 'None'}")
            
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
        
        # Update function_parameters with the new values
        function_parameters.update({
            "api_url": function_url,
            "response_timeout_secs": function_timeout or 20,
            "http_method": function_parameters.get("http_method", "POST")
        })
        
        # Build ElevenLabs tool config using existing function
        full_tool_config = build_elevenlabs_tool_config(function_parameters)
        
        # Extract the inner tool_config and update name/description
        tool_config = full_tool_config["tool_config"]
        tool_config["name"] = function_name
        tool_config["description"] = function_description
        
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
            
            print(f"🔍 Debug: Updating local database with:")
            print(f"🔍 Debug: - function_name: '{function_name}'")
            print(f"🔍 Debug: - function_description: '{function_description}'")
            print(f"🔍 Debug: - function_parameters: {function_parameters}")
            
            # Store old values for comparison
            old_name = function.tool_name
            old_description = function.tool_description
            old_config = function.tool_config
            
            function.tool_name = function_name
            function.tool_description = function_description
            function.tool_config = function_parameters
            
            print(f"🔍 Debug: Database changes:")
            print(f"🔍 Debug: - tool_name: '{old_name}' → '{function.tool_name}'")
            print(f"🔍 Debug: - tool_description: '{old_description}' → '{function.tool_description}'")
            print(f"🔍 Debug: - tool_config changed: {old_config != function.tool_config}")
            
            # Explicitly add the object to the session to ensure it's tracked
            db.session.add(function)
            print(f"🔍 Debug: Object added to session")
            
            # Commit the changes
            db.session.commit()
            print(f"✅ Debug: Database commit successful")
            
            # Force a flush to ensure changes are written to database
            db.session.flush()
            print(f"✅ Debug: Database flush completed")
            
            # Verify the data was actually saved by reading it back
            verification_function = ElevenLabsWebhookToolModel.get_by_id(function_id)
            print(f"🔍 Debug: Verification read - tool_description: '{verification_function.tool_description}'")
            
            # Also check if the object is dirty (has uncommitted changes)
            print(f"🔍 Debug: Function object dirty: {db.session.dirty}")
            print(f"🔍 Debug: Function object new: {db.session.new}")
            print(f"🔍 Debug: Function object deleted: {db.session.deleted}")
            
            # Prepare response data using the verification function
            function_data = {
                "id": verification_function.id,
                "function_name": verification_function.tool_name,
                "function_description": verification_function.tool_description,
                "function_url": function_parameters.get('api_url', ''),
                "function_timeout": function_parameters.get('response_timeout_secs', 20),
                "function_parameters": function_parameters
            }
        
        print(f"🔍 Debug: Response data being sent to frontend:")
        print(f"🔍 Debug: - id: {function_data['id']}")
        print(f"🔍 Debug: - function_name: '{function_data['function_name']}'")
        print(f"🔍 Debug: - function_description: '{function_data['function_description']}'")
        print(f"🔍 Debug: - function_url: '{function_data['function_url']}'")
        print(f"🔍 Debug: - function_timeout: {function_data['function_timeout']}")
        
        response = {
            "status": "success",
            "message": "Custom function updated successfully",
            "data": function_data
        }
        
        return JSONResponse(status_code=200, content=response)
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Something went wrong!", "error": str(e)})
