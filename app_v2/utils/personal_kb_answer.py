"""
Synthesizes a spoken-friendly answer from personal KB search results, using
the same Gemini model as llm_utils.py — so the search_personal_knowledge_base
tool returns a ready-to-relay answer instead of raw excerpts the agent's LLM
would otherwise have to synthesize itself mid-conversation.
"""

from typing import List, Optional
import google.generativeai as genai

from app_v2.core.config import VoiceSettings
from app_v2.core.logger import setup_logger
from app_v2.schemas.personal_knowledge_base_schema import PersonalKnowledgeBaseQueryResult

logger = setup_logger(__name__)

NO_RESULTS_ANSWER = "I couldn't find anything relevant to that in your knowledge base."

ANSWER_PROMPT_TEMPLATE = """You are answering a user's question during a live voice call, using ONLY the knowledge base excerpts below. Do not use any outside knowledge or add information that isn't in the excerpts. Give a concise, natural, spoken answer — no markdown, no bullet points, no source citations, nothing that reads awkwardly out loud. If the excerpts don't actually answer the question, say plainly that you don't have that information — never invent an answer.
{context_section}
User's question: {query}

Knowledge base excerpts:
{excerpts}

Answer:"""

_model: Optional[genai.GenerativeModel] = None


def _get_model(api_key: str) -> genai.GenerativeModel:
    """Configures the Gemini client and builds the model once, then reuses
    it — avoids the redundant genai.configure()/GenerativeModel() overhead
    on every single tool-search call."""
    global _model
    if _model is None:
        genai.configure(api_key=api_key)
        _model = genai.GenerativeModel("gemini-2.5-flash")
    return _model


async def generate_kb_answer(
    query: str,
    results: List[PersonalKnowledgeBaseQueryResult],
    conversation_context: Optional[str] = None,
) -> str:
    """
    Synthesizes a spoken-friendly answer to `query` from `results` (the
    top-matching KB chunks already retrieved for this agent). Falls back to
    the best single excerpt if there's no Gemini key configured or the LLM
    call fails, so a transient error never breaks the tool call entirely —
    and to a plain "not found" message if there are no results at all.
    """
    if not results:
        return NO_RESULTS_ANSWER

    api_key = VoiceSettings.GEMINI_API_KEY
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — falling back to raw top excerpt for personal KB answer")
        return results[0].content

    excerpts = "\n\n".join(
        f"{i + 1}. [{r.title or 'Untitled'}] {r.content}" for i, r in enumerate(results)
    )
    context_section = f"\nRecent conversation context: {conversation_context}\n" if conversation_context else ""
    prompt = ANSWER_PROMPT_TEMPLATE.format(
        context_section=context_section,
        query=query,
        excerpts=excerpts,
    )

    try:
        model = _get_model(api_key)
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(temperature=0.2),
        )
        if not response.text:
            raise RuntimeError("LLM returned empty response")
        return response.text.strip()
    except Exception as e:
        logger.error(f"Failed to synthesize personal KB answer, falling back to raw excerpt: {e}")
        return results[0].content
