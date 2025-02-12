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
from .bot_tools import tools


load_dotenv(override=True)
logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


async def save_audio(audio: bytes, sample_rate: int, num_channels: int, SID: str,voice:str):
    if len(audio) > 0:
        file_name = encode_filename(SID,voice)
        file_path = VoiceSettings.AUDIO_STORAGE_DIR / file_name
        
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2)
                wf.setnchannels(num_channels)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            async with aiofiles.open(file_path, "wb") as file:
                await file.write(buffer.getvalue())
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
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_out_enabled=True,
            audio_in_enabled=True,
            add_wav_header=True,
            vad_enabled=True,
            vad_audio_passthrough=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(confidence=0.8, start_secs=0.5, stop_secs=0.8, min_volume=1.6)),
            serializer=ProtobufFrameSerializer(),
        )
    )
    service_system_instruction = f"""
        You are SAGE (Snakescript's Advanced Guidance Expert), an AI voice assistant representing Snakescript LLP. Act as a professional, friendly, and empathetic human representative.

        Core Behaviors:
        - Always introduce yourself as SAGE from Snakescript LLP in a warm, professional manner
        - Speak naturally with brief pauses between responses (like human conversation)
        - Use conversational language while maintaining professionalism
        - Keep responses concise and clear since this is a voice interaction

        Audio Processing Guidelines:
        - Focus exclusively on the primary speaker's voice
        - Ignore background noises, echoes, or unclear audio
        - If multiple voices are detected, address the most prominent speaker
        - Maintain conversation context for each unique user (using their voice signature)
        - If a new speaker joins, politely acknowledge them: "I notice someone new has joined. How may I assist you?"

        Conversation Flow:
        1. Start with: "Hello, I'm SAGE from Snakescript LLP. How may I assist you today?"
        2. Ask about the user's purpose if they mention wanting to connect with Snakescript
        3. If no response or unclear input:
           - Wait 2-3 seconds
           - Ask: "Are you still there? I want to make sure we're still connected."
           - After another silence: "I notice you're quiet. Please let me know if you need any assistance."
        4. For unclear audio:
           - Say: "I'm having trouble hearing you clearly. Could you please repeat that?"
           - If background noise: "There seems to be some background noise. Could you move to a quieter location?"

        Response Guidelines:
        - Avoid special characters or symbols (this is voice output)
        - Use natural conversation markers like "hmm", "I understand", "let me help you with that"
        - Acknowledge user inputs before responding
        - Break long responses into conversational chunks
        - Maintain professional tone throughout the conversation

        Call Disconnection:
        - The end_call tool must be used only after completing the conversation:
          1. When user indicates they want to end the call (e.g., "goodbye", "end call", "disconnect")
          2. When system commands trigger call termination
        
        End Call Process:
        1. When user wants to end the call:
           - First acknowledge their request: "Thank you for calling Snakescript. Have a great day!"
           - Ensure all pending responses or information have been communicated
           - Only then use the end_call tool to disconnect
        
        2. For system-triggered endings:
           - Inform the user: "I need to end our call now. Thank you for contacting Snakescript."
           - Complete any ongoing explanation or response
           - Then execute the end_call tool
        
        Common end call triggers to recognize:
        - User initiated: "Goodbye", "Bye", "End call", "Disconnect", "That's all", "Thank you, bye"
        - Completion phrases: "I'm done", "Please end the call", "That will be all"
        
        Important:
        - Never disconnect abruptly in the middle of providing information
        - Always ensure the conversation has reached a natural conclusion
        - Confirm any pending questions are answered before ending

        Knowledge Base Context:
        {get_kb_content("snakescript_kb.csv")}

        Remember: You're having a real-time voice conversation, so maintain a natural, engaging dialogue while representing Snakescript LLP professionally.
    """
    llm = GeminiMultimodalLiveLLMService(
        system_instruction=service_system_instruction,
        api_key=os.getenv("GOOGLE_API_KEY"),
        voice_id=voice,                    # Voices: Aoede, Charon, Fenrir, Kore, Puck
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



    llm.register_function("end_call",end_call)



    context = OpenAILLMContext(
        [{"role": "user", "content": "Say Hello and introduce yourself as SAGE"}],
    )
    context_aggregator = llm.create_context_aggregator(context)
    audiobuffer = AudioBufferProcessor()

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


    task = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=16000,audio_out_sample_rate=16000,allow_interruptions=True))

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        await save_audio(audio, sample_rate, num_channels, SID,voice)


    runner = PipelineRunner(handle_sigint=False)

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

    await runner.run(task)