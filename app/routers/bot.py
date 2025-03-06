import os
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
from pipecat.services.gemini_multimodal_live.gemini import GeminiMultimodalLiveLLMService
from pipecat.serializers.twilio import TwilioFrameSerializer
from app.utils.helper import save_audio
# from app.services.bot_tools import end_call_tool
import asyncio, uuid
from fastapi.websockets import WebSocketState
from app.databases.models import TokensToConsume, UserModel
from app.databases.models import db

tools = [
    {
        "function_declarations": [
            {
                "name": "end_call",
                "description": "This Tool is Desgin to disconnect the call with client system command will run to disconnect the call so be ware of using this tool"
            },
            
        ]
    }
]


SAMPLE_RATE = 8000
load_dotenv(override=True)
async def run_bot(websocket_client, voice, stream_sid, welcome_msg, system_instruction='hello',knowledge_base=None, agent_id=None, user_id=None):
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_out_enabled=True,
            audio_in_enabled=True,
            add_wav_header=False if stream_sid else True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
            serializer= TwilioFrameSerializer(stream_sid) if stream_sid else ProtobufFrameSerializer(),
        )
    )
    tokens_to_consume = TokensToConsume.get_by_id(1).token_values

    llm = GeminiMultimodalLiveLLMService(
        system_instruction = f"""
                Say {welcome_msg} to the user first. Then, follow these instructions while answering the question: {system_instruction}

                **IMPORTANT**  

                1. **Answering Questions:**  
                - Use the following knowledge base to answer user questions: `{knowledge_base}`  


                2. **Handling Call Disconnection:**  
                - You have access to one function: `end_call()`, which disconnects the call with the client system.  
                - You should call this function when the user says phrases like:
                    - "ok bye"
                    - "goodbye"
                    - "thank you, bye"
                    - Or any similar farewell phrases.  
                - Before calling `end_call()`, politely acknowledge their goodbye and thank them for the conversation.  
                - Then, trigger the `end_call()` function to properly close the connection. 
                """,

        api_key=os.getenv("GOOGLE_API_KEY"),
        voice_id=voice,    
        tools = tools                
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



    llm.register_function("end_call",end_call)
    context = OpenAILLMContext([{"role": "user", "content":"Say Hello and introduce yourself"}],tools=tools)

    context_aggregator = llm.create_context_aggregator(context)
    audiobuffer = AudioBufferProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            context_aggregator.user(),
            llm,
            audiobuffer,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )


    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    runner = PipelineRunner(handle_sigint=False)

    async def deduct_tokens_periodically(user_id, tokens_to_consume, websocket_client):
        """
        Dynamically deduct tokens from the user's profile at an interval based on tokens per minute.
        Automatically disconnects the client when tokens reach 0.

        Args:
            user_id (int): The ID of the user.
            tokens_to_consume (int): Tokens to be consumed per minute.
            websocket_client: The WebSocket client instance to close the call.
        """
        if tokens_to_consume <= 0:
            logger.error("Invalid token consumption rate. It must be greater than 0.")
            return

        interval = 60 / tokens_to_consume
        logger.info(f"Starting token deduction every {interval:.2f} seconds ({tokens_to_consume} tokens/minute).")

        while True:
            with db():  
                user = db.session.query(UserModel).filter(UserModel.id == user_id).with_for_update().first()
                
                if user and user.tokens > 0:
                    user.tokens -= 1
                    db.session.commit()  
                    logger.info(f"Deducted 1 token. Remaining tokens: {user.tokens}")
                else:
                    logger.warning("User has run out of tokens. Disconnecting call...")

                    await websocket_client.close(code=1000, reason="Insufficient tokens")
                    logger.info("WebSocket connection closed due to insufficient tokens.")
                    break 

            await asyncio.sleep(interval)  



    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        if stream_sid:
            await save_audio(audio, sample_rate, num_channels, stream_sid, voice, int(agent_id))
        else:
            uid = uuid.uuid4()
            await save_audio(audio, sample_rate, num_channels, str(uid), voice, int(agent_id))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        tokens_to_consume = TokensToConsume.get_by_id(1).token_values
        if tokens_to_consume <= 0:
            logger.error("Invalid token rate; setting to default (10 tokens per minute).")
            tokens_to_consume = 10
        
        global token_task
        token_task = asyncio.create_task(deduct_tokens_periodically(user_id, tokens_to_consume, websocket_client))

        await audiobuffer.start_recording()
        a = await task.queue_frames([context_aggregator.user().get_context_frame()])
        print("Client connected:--", a)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await audiobuffer.stop_recording()
        await task.cancel()
        await runner.cancel()

        if token_task:
            token_task.cancel()
            logger.info("Stopped token deduction as client disconnected.")

    await runner.run(task)