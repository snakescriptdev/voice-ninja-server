import os, json
import sys
from dotenv import load_dotenv
import pandas as pd
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.services.gemini_multimodal_live.gemini import GeminiMultimodalLiveLLMService, InputParams
from pipecat.serializers.twilio import TwilioFrameSerializer
from app.utils.helper import save_audio, send_request, save_conversation
# from app.services.bot_tools import end_call_tool
import asyncio, uuid
from fastapi.websockets import WebSocketState
from datetime import datetime
from app.databases.models import TokensToConsume, UserModel, AgentModel, CallModel, CustomFunctionModel, WebhookModel, OverallTokenLimitModel, DailyCallLimitModel,agent_knowledge_association
from app.databases.models import db
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.services.gemini_multimodal_live.gemini import GeminiMultimodalModalities
from app.utils.langchain_integration import retrieve_from_vectorstore
from pipecat.frames.frames import FunctionCallResultProperties



def generate_json_schema(dynamic_fields):
    schema = {
        "type": "object",
        "properties": {},
        "required": list(dynamic_fields.keys())
    }

    for field, description in dynamic_fields.items():
        schema["properties"][field] = {
            "type": "string",  # Assuming all fields are strings
            "description": description
        }

    return json.dumps(schema, indent=4)

tools = [
            {
                "function_declarations": [
                    {
                        "name": "end_call",
                        "description": "This Tool is designed to disconnect the call with a client system command. Use it with caution.",
                    },
                    {
                        "name": "retrieve_text_from_vectorstore",
                        "description": "This Tool is designed to retrieve text from a vector store. Use it with caution.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "The query to search the vector store with."}
                            },
                            "required": ["query"]
                        }
                    }
                ]
            }
        ]


SAMPLE_RATE = 8000
load_dotenv(override=True)
async def run_bot(websocket_client, voice, stream_sid, welcome_msg, system_instruction='hello',knowledge_base=None, agent_id=None, user_id=None, dynamic_variables=None, uid=None, custom_functions_list=None, temperature=None, max_output_tokens=None):
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_out_enabled=True,
            audio_in_enabled=True,
            add_wav_header=not bool(stream_sid),
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
            serializer=TwilioFrameSerializer(stream_sid) if stream_sid else ProtobufFrameSerializer(),
        )
    )
    
    conversation_list = []
    tokens_to_consume = TokensToConsume.get_by_id(1).token_values
    if temperature:
        temperature = float(temperature)
    else:
        temperature = 0.7
    if max_output_tokens:
        max_output_tokens = int(max_output_tokens)
    else:
        max_output_tokens = 4096

    if dynamic_variables:
        dynamic_fields = generate_json_schema(dynamic_variables)
        tools[0]["function_declarations"].insert(0, {  # Insert at index 0
            "name": "set_dynamic_variable",
            "description": "This tool is designed to set the dynamic variables for the call.",
            "parameters": json.loads(dynamic_fields)
        })

    if custom_functions_list:
        for function in custom_functions_list:
            if "parameters" in function and isinstance(function["parameters"], dict):
                tools[0]["function_declarations"].insert(1, {  # Insert after dynamic_variables
                    "name": function["name"],
                    "description": function["description"],
                    "parameters": function["parameters"]
                })

    llm = GeminiMultimodalLiveLLMService(
        system_instruction=f"""
            Say {welcome_msg} to the user first. Then, follow these instructions while answering the question: {system_instruction}

            **IMPORTANT**  

            1. **Handling Answering Questions:**  
            - If the user asks a question that is not in the knowledge base:
                1. First respond with "Please wait a moment while I search for that information in my knowledge base."
                2. Then use the `retrieve_text_from_vectorstore()` function to search for relevant information
                3. this vectorstore have data about the product, services, and other information.
                4. If results are found, provide the answer based on the retrieved documents
                5. If no results are found, inform the user that you could not find relevant information and ask them to provide more information or to try another query.
            - The `retrieve_text_from_vectorstore()` function takes a query as input and returns a list of documents that are relevant to the query.
    
            2. **Handling Dynamic Variables:**  
            - You have access to one function: `set_dynamic_variable()`, which sets the dynamic variables for the call.  
            - You should call this function when the user provides all their information details.
            - IMPORTANT: Once you've collected ALL the necessary information from the customer, immediately call the `set_dynamic_variable()` function to store the complete information in the database.
            - Example scenario: After the customer has shared all their details (name, email, contact information, etc.), summarize what you've understood, then call the `set_dynamic_variable()` function before continuing the conversation.

            3. **Custom Functions:**
                - You have access to the following custom functions: {', '.join([f"`{func['name']}`" for func in custom_functions_list]) if custom_functions_list else "None"}
                - Call these functions when appropriate based on the following triggers:
                {
                    '\\n'.join([f"- `{func['name']}`: Call when {func.get('description', 'needed')}" for func in custom_functions_list]) if custom_functions_list else ""
                }
                - When calling a custom function, acknowledge to the user what you're doing, then call the function, and explain the result to the user.

            4. **Handling Call Disconnection:**  
            - You have access to one function: `end_call()`, which disconnects the call with the client system.  
            - You should call this function when the user says phrases like:
                - "ok bye"
                - "goodbye"
                - "thank you, bye"
                - Or any similar farewell phrases.  
            - Before calling `end_call()`, politely acknowledge their goodbye and thank them for the conversation.  
            - Then, trigger the `end_call()` function to properly close the connection. 
            - If the user asks a question that you don't know the answer to, use the `retrieve_text_from_vectorstore()` function to answer the question.
        """,
        api_key=os.getenv("GOOGLE_API_KEY"),
        voice_id=voice,    
        tools=tools,
        transcribe_user_audio=True,
        transcribe_model_audio=True,
        params=InputParams(temperature=temperature, max_tokens=max_output_tokens, modalities=GeminiMultimodalModalities.AUDIO)          
    )


    async def end_call(function_name, tool_call_id, args, llm, context, result_callback) -> dict:
        """
        Handles the end call functionality
        
        Args:
            function_name (str): Name of the function being called
            tool_call_id (str): ID of the tool call
            args (dict): Arguments passed to the function
            llm (object): LLM service instance
            context (object): Conversation context
            result_callback (callable): Callback for results
            
        Returns:
            dict: Response containing status and message
        """
        try:
            async def delayed_close():
                try:
                    await asyncio.sleep(5) 
                    logger.info(f"Websocket client state: {websocket_client.client_state}")
                    
                    if websocket_client.client_state != WebSocketState.DISCONNECTED:
                        await websocket_client.close(code=1000, reason="Call ended normally")
                        logger.info("Websocket closed successfully after delay")
                except Exception as e:
                    logger.error(f"Error in delayed close: {str(e)}")

            asyncio.create_task(delayed_close())
            

            return {"status": "success", "message": "Call end initiated"}
            
        except Exception as e:
            logger.error(f"Error in end_call: {str(e)}")
            return {"status": "error", "message": f"Error processing end call: {str(e)}"}
        
    async def set_dynamic_variable(function_name, tool_call_id, args, llm, context, result_callback) -> dict:
        """
        Handles the set dynamic variable functionality
        
        Args:
            function_name (str): Name of the function being called
            tool_call_id (str): ID of the tool call
            args (dict): Arguments passed to the function
            llm (object): LLM service instance
            context (object): Conversation context
            result_callback (callable): Callback for results

        Returns:
            dict: Response containing status and message
        """
        try:            
            # Get the call model from the database
            agent = AgentModel.get_by_id(agent_id)
            if agent:        
                call = CallModel.create(agent_id=agent_id,call_id=uid, variables=args)
                webhook = WebhookModel.get_by_user(user_id)
                if webhook:
                   response = await send_request(webhook.webhook_url, args)
                return {"status": "success", "message": "Call details saved successfully"}
            else:
                return {"status": "success", "message": "Agent not found"}
        except Exception as e:
            logger.error(f"Error in set_dynamic_variable: {str(e)}")
            return {"status": "error", "message": f"Error processing set dynamic variable: {str(e)}"}
    
    async def custom_function(function_name, tool_call_id, args, llm, context, result_callback) -> dict:
        try:
            agent = AgentModel.get_by_id(agent_id)
            if agent: 
                custom_function = CustomFunctionModel.get_by_name(function_name, agent_id)
                if custom_function:
                    custom_function_url = custom_function.function_url
                    response = await send_request(custom_function_url, args)
                    return {"status": "success", "message": "Custom function executed successfully"}
                else:
                    return {"status": "error", "message": "Custom function not found"}
            else:
                return {"status": "error", "message": "Agent not found"}
        except Exception as e:
            logger.error(f"Error in custom_function: {str(e)}")
            return {"status": "error", "message": f"Error processing custom function: {str(e)}"}
    

    async def retrieve_text_from_vectorstore(function_name, tool_call_id, args, llm, context, result_callback) -> dict:
        try:
            agent = AgentModel.get_by_id(agent_id)
            if agent:
                from sqlalchemy.orm import sessionmaker
                from app.databases.models import engine
                from sqlalchemy import select
                from app.databases.models import agent_knowledge_association, KnowledgeBaseModel
                Session = sessionmaker(bind=engine)
                session = Session() 
        
                query = select(agent_knowledge_association).where(
                    agent_knowledge_association.c.agent_id == agent_id
                )
                result = session.execute(query)
                existing_association = result.fetchone()
                knowledge_base = KnowledgeBaseModel.get_by_id(existing_association.knowledge_base_id)
                response = await retrieve_from_vectorstore(args["query"], knowledge_base.vector_path, knowledge_base.vector_id, 5)
                data = response
                await result_callback(data)
            else:
                return {"status": "error", "message": "Agent not found"}
        except Exception as e:
            logger.error(f"Error in retrieve_from_vectorstore: {str(e)}")
            data = "Something went wrong while searching in the vector store"
            properties = FunctionCallResultProperties(run_llm=True)
            await result_callback(data, properties=properties)

    
    llm.register_function("retrieve_text_from_vectorstore",retrieve_text_from_vectorstore)

    if dynamic_variables:
        llm.register_function("set_dynamic_variable",set_dynamic_variable)
    if custom_functions_list:
        for function in custom_functions_list:
            llm.register_function(function["name"], custom_function)

    llm.register_function("end_call",end_call)
    context = OpenAILLMContext([{"role": "user", "content":" "}],tools=tools)
    context_aggregator = llm.create_context_aggregator(context)
    audiobuffer = AudioBufferProcessor()
    transcript = TranscriptProcessor()
    pipeline = Pipeline(
        [
            transport.input(),                # Handles incoming user data
            context_aggregator.user(),        # Manages user context
            llm,                              # Processes input using the LLM service
            audiobuffer,                      # Buffers audio for processing
            transport.output(),               # Sends responses back to the user
            transcript.user(),                # Logs user inputs
            transcript.assistant(),           # Logs assistant responses
            context_aggregator.assistant(),   # Manages assistant context
        ]
    )

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    runner = PipelineRunner(handle_sigint=False)


    async def deduct_tokens_periodically(user_id, tokens_to_consume, agent_id, websocket_client):
        """
        Deducts tokens periodically and enforces token/call limits.
        """
        if tokens_to_consume <= 0:
            logger.error("Invalid token consumption rate. Must be greater than 0.")
            return

        interval = 60 / tokens_to_consume
        logger.info(f"Starting token deduction every {interval:.2f} seconds")
        
        agent = AgentModel.get_by_id(agent_id)
        per_call_tokens = 0
        
        while True:
            try:
                with db():
                    # Get all required models in one query
                    user, overall_limit, daily_limit = (
                        db.session.query(
                            UserModel,
                            OverallTokenLimitModel,
                            DailyCallLimitModel
                        ).filter(
                            UserModel.id == user_id,
                            OverallTokenLimitModel.agent_id == agent_id,
                            DailyCallLimitModel.agent_id == agent_id
                        ).with_for_update().first()
                    )

                    if not user or user.tokens <= 0:
                        await close_websocket(websocket_client, "Insufficient tokens")
                        break

                    # Check overall token limit
                    if overall_limit and overall_limit.last_used_tokens >= overall_limit.overall_token_limit:
                        await close_websocket(websocket_client, "Overall token limit exceeded")
                        break

                    # Check daily call limit
                    if daily_limit:
                        if should_reset_daily_limit(daily_limit.last_updated):
                            daily_limit.last_used = 0
                        
                        if daily_limit.last_used >= daily_limit.set_value:
                            await close_websocket(websocket_client, "Daily call limit exceeded")
                            break
                        
                        daily_limit.last_used += 1
                        daily_limit.last_updated = datetime.utcnow()
                        logger.info(f"Updated daily call usage: {daily_limit.last_used}")

                    # Check per call token limit
                    if agent.per_call_token_limit > 0:
                        per_call_tokens += 1
                        if per_call_tokens >= agent.per_call_token_limit:
                            await close_websocket(websocket_client, "Per call token limit exceeded")
                            break

                    # Update token counts
                    user.tokens -= 1
                    if overall_limit:
                        overall_limit.last_used_tokens += 1
                    
                    db.session.commit()
                    logger.info(f"Tokens remaining: {user.tokens}, Overall usage: {overall_limit.last_used_tokens if overall_limit else 'N/A'}")

            except Exception as e:
                logger.error(f"Error in token deduction: {e}")
                await close_websocket(websocket_client, "Internal error during token processing")
                break

            await asyncio.sleep(interval)

    async def close_websocket(websocket, reason):
        """Helper to close websocket with logging"""
        logger.warning(f"{reason}. Disconnecting call...")
        await websocket.close(code=1000, reason=reason)
        logger.info(f"WebSocket connection closed: {reason}")

    def should_reset_daily_limit(last_updated):
        """Check if daily limit should reset"""
        return (datetime.utcnow() - last_updated).total_seconds() >= 86400





    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        if stream_sid:
            await save_audio(audio, sample_rate, num_channels, stream_sid, voice, int(agent_id))
        else:
            await save_audio(audio, sample_rate, num_channels, uid, voice, int(agent_id))


    @transcript.event_handler("on_transcript_update")
    async def handle_update(processor, frame):
        try:
            for msg in frame.messages:
                conversation_list.append({"role": msg.role, "content": msg.content})
        except Exception as e:
            logger.error(f"Error processing user transcript: {e}")


    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        tokens_to_consume = TokensToConsume.get_by_id(1).token_values
        if tokens_to_consume <= 0:
            logger.error("Invalid token rate; setting to default (10 tokens per minute).")
            tokens_to_consume = 10
        
        global token_task
        token_task = asyncio.create_task(deduct_tokens_periodically(user_id, tokens_to_consume, agent_id, websocket_client))

        await audiobuffer.start_recording()
        a = await task.queue_frames([context_aggregator.user().get_context_frame()])
        print("Client connected:--", a)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await audiobuffer.stop_recording()
        await task.cancel()
        await runner.cancel()
        if stream_sid:
            await save_conversation(conversation_list, stream_sid)
        else:
            await save_conversation(conversation_list,  uid)
        if token_task:
            token_task.cancel()
            logger.info("Stopped token deduction as client disconnected.")
        conversation_list.clear()

    await runner.run(task)