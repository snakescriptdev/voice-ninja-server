import os
import google.generativeai as genai
from app_v2.schemas.agent_config import AgentConfigGenerator
from app_v2.core.logger import setup_logger
from typing import Optional
from pydantic import ValidationError

logger = setup_logger(__name__)


SYSTEM_PROMPT_TEMPLATE = """
You are an AI system prompt generator.

Generate a clear, production-ready system prompt for a voice AI agent using the following configuration.

Agent Name: {agent_name}
Language: {language}
Main Goal: {main_goal}

Use Cases:
{use_cases}

Capebilities:
{capebilites}

Voice: {voice}
AI Model: {ai_model}
Response Style: {response_style}

Rules:
- Output ONLY the final system prompt
- Do not explain anything
- Keep it concise but complete
- Optimize for real-time voice conversations
"""


async def generate_system_prompt_async(
    config: AgentConfigGenerator,
) -> str:
    """
    Generates a system prompt using Google Gemini (async) via official SDK.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        raise RuntimeError("GEMINI_API_KEY environment variable not set")

    genai.configure(api_key=api_key)

    logger.info(
        "Generating system prompt for agent=%s model=%s",
        config.agent_name,
        config.ai_model,
    )

    try:
        # Construct the prompt
        formatted_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            agent_name=config.agent_name,
            language=config.language,
            main_goal=config.main_goal,
            use_cases="\n".join(f"- {u}" for u in config.use_cases),
            capebilites="\n".join(f"- {c}" for c in config.capebilites),
            voice=config.voice,
            ai_model=config.ai_model,
            response_style=config.response_style,
        )

        model = genai.GenerativeModel("gemini-2.5-flash")
        
        response = await model.generate_content_async(
            formatted_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.4
            )
        )

        if not response.text:
            logger.error("Empty response from Gemini")
            raise RuntimeError("LLM returned empty response")

        logger.info("System prompt generated successfully")
        return response.text.strip()

    except ValidationError as e:
        logger.exception("Invalid AgentConfigGenerator payload")
        raise ValueError("Invalid agent configuration") from e
    except Exception as e:
        logger.exception("Unexpected error while generating system prompt")
        # Log basic details if it's an API error
        raise RuntimeError(f"Unexpected system prompt generation error: {str(e)}") from e




