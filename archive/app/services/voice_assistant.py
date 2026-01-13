import os
import sys
import io
import wave
import aiofiles
from pipecat.audio.vad.vad_analyzer import VADParams
from fastapi.websockets import WebSocketState
import asyncio
from dotenv import load_dotenv
import pandas as pd
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
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
from app.utils import encode_filename
from app.core import VoiceSettings
from .bot_tools import tools, VOICE_ASSISTANT_PROMPT
from app.databases.models import AudioRecordModel
import soundfile as sf

load_dotenv(override=True)
logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


async def save_audio(audio: bytes, sample_rate: int, num_channels: int, SID: str, voice: str, record_id: int):
    if len(audio) > 0:
        file_name = encode_filename(SID, voice)
        file_path = VoiceSettings.AUDIO_STORAGE_DIR / file_name
        
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2)
                wf.setnchannels(num_channels)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            async with aiofiles.open(file_path, "wb") as file:
                await file.write(buffer.getvalue())
        duration = sf.info(file_path).duration
        audio_record = AudioRecordModel.get_by_id(record_id)
        audio_record.update(file_path=str(file_path), file_name=file_name, duration=duration)
        logger.info(f"Audio saved to {file_path}")
    else:
        logger.info("No audio data to save")

def get_kb_content(csv_path: str) -> str:
    """
    Get knowledge base content in a readable string format
    
    Args:
        csv_path (str): Path to the CSV file
        
    Returns:
        str: Formatted string containing KB content
    """
    try:
        # Read CSV file
        df = pd.read_csv(csv_path)
        
        # Format content
        content_parts = []
        for idx, row in df.iterrows():
            question = row['question'].strip()
            answer = row['answer'].strip()
            content_parts.append(f"Q: {question}\nA: {answer}\n")
            
        # Join all parts with separator
        formatted_content = "\n".join(content_parts)
        
        return formatted_content
    
    except Exception as e:
        logger.error(f"Error reading KB content: {str(e)}")
        return "Error: Unable to read knowledge base content"

async def RunAssistant(websocket_client, voice, uid):
    SID = uid
    audio_record = AudioRecordModel.create_record(file_path="", file_name="", voice=voice, duration=0, email="", number="")
    print(audio_record.id)
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_out_enabled=True,
            audio_in_enabled=True,
            add_wav_header=True,
            vad_enabled=True,
            vad_audio_passthrough=True,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=ProtobufFrameSerializer(),
        )
    )
    client_name = "YOU NEED ASK NAME OF CLIENT"
    client_call_purpose = "YOU NEED ASK PURPOSE OF CALL"

    llm = GeminiMultimodalLiveLLMService(
        system_instruction=VOICE_ASSISTANT_PROMPT.format(call_id=audio_record.id, snakesscript_knowledge=get_kb_content("snakescript_kb.csv"), client_name=client_name, client_call_purpose=client_call_purpose),
        api_key=os.getenv("GOOGLE_API_KEY"),
        voice_id=voice,
        tools = tools,
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
            # Create a background task for sleeping and closing
            async def delayed_close():
                try:
                    await asyncio.sleep(5)
                    logger.info(f"Websocket client state: {websocket_client.client_state}")
                    if websocket_client.client_state != WebSocketState.DISCONNECTED:
                        await websocket_client.close(code=1000, reason="Call ended normally")
                        logger.info("Websocket closed successfully after delay")
                except Exception as e:
                    logger.error(f"Error in delayed close: {str(e)}")

            # Schedule the delayed close in the background
            asyncio.create_task(delayed_close())
            
            # Return immediately while close happens in background
            return {"status": "success", "message": "Call end initiated"}
            
        except Exception as e:
            logger.error(f"Error in end_call: {str(e)}")
            return {"status": "error", "message": f"Error processing end call: {str(e)}"}
    
    async def submit_email_number(function_name, tool_call_id, args, llm, context, result_callback) -> dict:
        logger.info(f"Submit email number tool called with args: {args}")
        audio_record = AudioRecordModel.get_by_id(args["call_id"])
        audio_record.update(email=args.get("email", ""), number=args.get("number", ""))
        logger.info(f"Audio record updated: {audio_record.id}")
        await result_callback([
                    {
                        "role": "system",
                        "content": "Contact information stored successfully continue with your conversation",
                    }
                ])

    async def get_call_availability(function_name, tool_call_id, args, llm, context, result_callback) -> dict:
        await result_callback([
                    {
                        "role": "system",
                        "content": "Yes Sales is available for call please schedule a call with client for further discussion",
                    }
                ])

    llm.register_function("end_call",end_call)
    llm.register_function("store_client_contact_details",submit_email_number)
    llm.register_function("get_call_availability",get_call_availability)



    context = OpenAILLMContext(
        [{"role": "user", "content": "Say Hello and introduce yourself as SAGE"}],
        tools=tools
    )
    context_aggregator = llm.create_context_aggregator(context)
    audiobuffer = AudioBufferProcessor(user_continuous_stream=True)

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from client
            context_aggregator.user(),
            llm,  # LLM
            transport.output(),  # Websocket output to client
            audiobuffer,
            context_aggregator.assistant(),
        ]
    )


    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        await save_audio(audio, sample_rate, num_channels, SID, voice, audio_record.id)


    runner = PipelineRunner(handle_sigint=False,force_gc=True)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await audiobuffer.start_recording()
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        if audiobuffer.has_audio():
            logger.info("Audio buffer has audio")
        await audiobuffer.stop_recording()
        await task.cancel()
        await runner.cancel()
    try:
        await runner.run(task)
    except Exception as ex:
        print(f'ex: {str(ex)}')