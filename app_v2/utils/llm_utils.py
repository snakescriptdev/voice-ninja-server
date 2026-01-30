import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from app_v2.schemas.agent_config import AgentConfigGenerator
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


SYSTEM_PROMPT_TEMPLATE = """
You are an AI system prompt generator.

Generate a clear, production-ready system prompt for a voice AI agent using the following configuration.

Agent Name: {agent_name}
Language: {language}
Main Goal: {main_goal}

Use Cases:
{use_cases}

Capabilities:
{capabilites}

Voice: {voice}
AI Model: {ai_model}
Response Style: {response_style}

Rules:
- Output ONLY the final system prompt
- Do not explain anything
- Keep it concise but complete
- Optimize for real-time voice conversations
"""


api_key = os.getenv("GEMINI_API_KEY")


from typing import Optional
from pydantic import ValidationError

async def generate_system_prompt_async(
    config: AgentConfigGenerator,
) -> str:
    """
    Generates a system prompt using Google Gemini (async).
    """

    logger.info(
        "Generating system prompt for agent=%s model=%s",
        config.agent_name,
        config.ai_model,
    )

    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash", 
            temperature=0.4,
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT_TEMPLATE),
            ("user", "Generate the system prompt based on the above configuration.")
        ])

        chain = prompt | llm

        response = await chain.ainvoke({
            "agent_name": config.agent_name,
            "language": config.language,
            "main_goal": config.main_goal,
            "use_cases": "\n".join(f"- {u}" for u in config.use_cases),
            "capabilites": "\n".join(f"- {c}" for c in config.capabilites),
            "voice": config.voice,
            "ai_model": config.ai_model,
            "response_style": config.response_style,
        })

        if not response or not response.content:
            logger.error("Empty response from Gemini")
            raise RuntimeError("LLM returned empty response")

        logger.info("System prompt generated successfully")
        return response.content.strip()

    except ValidationError as e:
        logger.exception("Invalid AgentConfigGenerator payload")
        raise ValueError("Invalid agent configuration") from e
    except Exception as e:
        logger.exception("Unexpected error while generating system prompt")
        raise RuntimeError("Unexpected system prompt generation error") from e




