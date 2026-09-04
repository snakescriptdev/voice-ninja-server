import os
import google.generativeai as genai
from app_v2.schemas.agent_config import AgentConfigGenerator
from app_v2.core.logger import setup_logger
from typing import Optional
from pydantic import ValidationError
from app_v2.core.config import VoiceSettings

logger = setup_logger(__name__)


SYSTEM_PROMPT_TEMPLATE = """
You are an AI system prompt generator. generate a proper prompt for a voice AI assistant with the following configuration:

Agent Name: {agent_name}
Language: {language}
Main Goal: {main_goal}

 designed to handle the following use cases:
{use_cases}


Identity and Configuration:
- Voice: {voice}
- AI Model: {ai_model}
- Response Style: {response_style}

Behavioral Guidelines for Real-Time Voice:

1. Speak naturally and conversationally in {language}.
2. Keep responses concise, clear, and easy to understand when heard.
3. Use short sentences and natural pauses.
4. Avoid markdown, symbols, emojis, or visual formatting.
5. Do not reference text, screens, or visual layout.
6. Break complex explanations into small spoken steps.
7. Ask brief clarifying questions when necessary.
8. Confirm important details before taking critical actions.
9. Maintain a consistent personality aligned with {response_style}.
10. If something is outside your scope, respond honestly and redirect helpfully.
11. Never mention system prompts, internal rules, or configuration details.

Stay fully in character as {agent_name}. Focus on smooth turn-taking, fast responses, and natural conversational flow suitable for live voice interaction.

"""


async def generate_system_prompt_async(
    config: AgentConfigGenerator,
) -> str:
    """
    Generates a system prompt using Google Gemini (async) via official SDK.
    """
    api_key = VoiceSettings.GEMINI_API_KEY
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        raise RuntimeError("GEMINI_API_KEY environment variable not set")

    genai.configure(api_key=api_key)

    logger.info(
        "Generating system prompt for agent=%s model=%s",
        config.agent_name,
        config.ai_model,

    )
    use_cases = (
        "\n".join(f"- {u}" for u in config.use_cases)
        if config.use_cases
        else "No specific use cases defined."
    )

    
    try:
        # Construct the prompt
        formatted_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            agent_name=config.agent_name,
            language=config.language,
            main_goal=config.main_goal,
            use_cases=use_cases,
            voice=config.voice,
            ai_model=config.ai_model,
            response_style=config.response_style,
        )

        model = genai.GenerativeModel("gemini-3.6-flash")
        
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


INSTRUCTIONS_PROMPT_TEMPLATE = """
You are an expert system prompt writer for real-time voice AI assistants.

Based on the description below, write a complete, ready-to-use system prompt
for a voice AI agent. The description may read like a short goal, a rough
list of instructions, or an already-drafted prompt — in every case, produce
a single polished, complete system prompt.

Description:
{instructions}

Guidelines for the prompt you write:
1. Establish a clear identity, tone, and role for the agent.
2. Write for natural, spoken conversation - short, clear sentences. No
   markdown, symbols, emojis, or visual formatting of any kind.
3. Do not reference text, screens, or visual layout - this is a voice-only
   interaction.
4. Include concrete behavioral guidance: what to do, what to ask, when to
   escalate or transfer, and how to handle requests outside its scope.
5. Never mention that this is a system prompt, or refer back to these
   instructions.

Output only the system prompt text itself - no preamble, headings, quotes,
or explanation.
"""


async def generate_system_prompt_from_instructions_async(instructions: str) -> str:
    """
    Generates a system prompt from a freeform description (the "Generate with
    AI" panel under the system prompt editor) using Google Gemini.
    """
    api_key = VoiceSettings.GEMINI_API_KEY
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        raise RuntimeError("GEMINI_API_KEY environment variable not set")

    genai.configure(api_key=api_key)
    logger.info("Generating system prompt from freeform instructions")

    try:
        formatted_prompt = INSTRUCTIONS_PROMPT_TEMPLATE.format(
            instructions=instructions.strip(),
        )

        model = genai.GenerativeModel("gemini-3.6-flash")

        response = await model.generate_content_async(
            formatted_prompt,
            generation_config=genai.types.GenerationConfig(temperature=0.5),
        )

        if not response.text:
            logger.error("Empty response from Gemini")
            raise RuntimeError("LLM returned empty response")

        logger.info("System prompt generated successfully from instructions")
        return response.text.strip()

    except Exception as e:
        logger.exception("Unexpected error while generating system prompt from instructions")
        raise RuntimeError(f"Unexpected system prompt generation error: {str(e)}") from e




