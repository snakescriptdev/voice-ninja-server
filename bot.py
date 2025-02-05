import os
import sys

import boto3
from dotenv import load_dotenv
from loguru import logger
from datetime import datetime
import pandas as pd
from typing import Optional

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
SAMPLE_RATE = 24000
load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

tools = [
    {
        "function_declarations": [
            {
                "name": "payment_kb",
                "description": "Used to get any snakescript company-related FAQ or details",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "The query or question related to snakescript company."
                        }
                    },
                    "required": ["input"]
                }
            }
        ]
    }
]


system_instruction =  """
    Always start with "I am Sage, an AI empowered agent. How can I help you today?"
    I am SAGE, your engaging voice assistant for this conversation. My responses will be:
    - Brief and clear (aim for 2-3 sentences when possible)
    - Natural and conversational, not robotic
    - Easy to understand over the phone

    Guidelines for responses:
    1. Keep answers concise and informative:
       - Focus on the most relevant information
       - Use clear, everyday language
       - Add brief context only when necessary for clarity
    
    2. Maintain a helpful and friendly tone:
       - Be warm and approachable
       - Show empathy and understanding
       - Stay professional while being conversational
    
    3. Structure responses effectively:
       - Start with the most important information
       - Use simple, direct language
       - Provide actionable insights when applicable

    Remember:
    - If asked about my name, explain that SAGE stands for Snakescript's Advanced Guidance Expert
    - Speak as if having a friendly phone conversation
    - Avoid technical jargon unless specifically asked
    - If you need to list items, limit to 3 key points
    - Use natural transitions and acknowledgments (e.g., "I understand...", "Great question...", "Ah, I see...", "Uh-huh", "Mm-hmm")
"""

def load_kb_from_csv(csv_path: str) -> pd.DataFrame:
    """Load knowledge base from CSV file"""
    return pd.read_csv(csv_path)

def query_kb(df: pd.DataFrame, query: str) -> Optional[str]:
    """Query the knowledge base using simple keyword matching"""
    # Convert query to lowercase for case-insensitive matching
    query = query.lower()
    
    # Search through questions/keywords column (adjust column name as needed)
    for idx, row in df.iterrows():
        if row['question'].lower() in query:
            return row['answer']
    
    # If no match found, return None
    return None

def payment_kb(input: str) -> str:
    """Can be used to get any payment related FAQ/ details"""
    kb_df = load_kb_from_csv("snakescript_kb.csv")
    
    # Try to find answer in knowledge base
    answer = query_kb(kb_df, input)
    
    if answer:
        return answer
    
    # If no answer found in KB, use default response
    default_response = """I apologize, but I don't have specific information about that query. 
    Please contact our support team for accurate information."""
    return default_response

async def run_bot(websocket_client):
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_out_sample_rate=24000,
            audio_out_enabled=True,
            add_wav_header=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
            serializer=ProtobufFrameSerializer()
        )
    )
    llm = GeminiMultimodalLiveLLMService(
        api_key=os.getenv("GOOGLE_API_KEY"),
        system_instruction=system_instruction,
        tools=tools,
        voice_id="Aoede",                    # Voices: Aoede, Charon, Fenrir, Kore, Puck
        transcribe_user_audio=True,          # Enable speech-to-text for user input
        transcribe_model_audio=True,         # Enable speech-to-text for model responses
    )
    llm.register_function("get_payment_info", payment_kb)



    context = OpenAILLMContext(
        
        [{"role": "user", "content": "Say hello."}],
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


    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # Kick off the conversation.
        # messages.append({"role": "system", "content": "Please introduce yourself to the user."})
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner(handle_sigint=False)

    await runner.run(task)