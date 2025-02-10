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
load_dotenv(override=True)
logger.remove(0)
logger.add(sys.stderr, level="DEBUG")
import io
import wave
import aiofiles
from .config import AUDIO_STORAGE_DIR, SAMPLE_RATE

# Update the save_audio function in bot.py
async def save_audio(audio: bytes, sample_rate: int, num_channels: int, SID: str):
    if len(audio) > 0:
        filename = f"{SID}.wav"
        file_path = AUDIO_STORAGE_DIR / filename
        
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2)
                wf.setnchannels(num_channels)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            async with aiofiles.open(file_path, "wb") as file:
                await file.write(buffer.getvalue())
        print(f"Audio saved to {file_path}")
    else:
        print("No audio data to save")

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

async def run_bot(websocket_client, voice, uid):
    SID = uid
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            audio_out_enabled=True,
            audio_in_enabled=True,
            add_wav_header=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
            serializer=ProtobufFrameSerializer(),
        )
    )
    service_system_instruction = f"""
        You are a helpful LLM Snakescript's Advanced Guidance Expert (SAGE) in Snakescript LLP Company.
        Your goal is to demonstrate your capabilities in a succinct way.
        Your output will be converted to audio so don't include special characters in your answers. 
        Respond to what the user said in a creative and helpful way.
        there tools (get_snakescript_info) are available to you to get information about the company(snakescript) and its products, service , voice agent ,web development list of sectors.
        
        ### Knowledge Base ###
        {get_kb_content("snakescript_kb.csv")}
    """
    llm = GeminiMultimodalLiveLLMService(
        system_instruction=service_system_instruction,
        api_key=os.getenv("GOOGLE_API_KEY"),
        voice_id=voice,                    # Voices: Aoede, Charon, Fenrir, Kore, Puck
    )



    context = OpenAILLMContext(
        [{"role": "user", "content": "Say Hello and introduce yourself as SAGE"}],
    )
    context_aggregator = llm.create_context_aggregator(context)
    audiobuffer = AudioBufferProcessor(sample_rate=SAMPLE_RATE)

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


    task = PipelineTask(pipeline, params=PipelineParams())

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        await save_audio(audio, sample_rate, num_channels, SID)


    runner = PipelineRunner(handle_sigint=False)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await audiobuffer.start_recording()
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        if audiobuffer.has_audio():
            print("Audio buffer has audio")
        await audiobuffer.stop_recording()
        await task.cancel()
        await runner.cancel()

    await runner.run(task)