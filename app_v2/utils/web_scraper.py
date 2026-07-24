"""URL scraping for the pgvector-backed personal knowledge base, via CrewAI's ScrapeWebsiteTool."""

from fastapi import HTTPException
from app_v2.core.logger import setup_logger
from app_v2.utils.scraping_utils import scrape_webpage_title

logger = setup_logger(__name__)


def scrape_url(url: str) -> tuple[str, str]:
    """
    Scrape a URL's page content for embedding, plus its <title> for display.

    Returns:
        (title, text)
    """
    title = scrape_webpage_title(url)

    try:
        from crewai_tools import ScrapeWebsiteTool

        tool = ScrapeWebsiteTool(website_url=url)
        text = tool.run()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to scrape URL '{url}': {e}")
        raise HTTPException(status_code=422, detail="Failed to extract content from this URL.")

    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="No readable content could be extracted from this URL.")

    return title, text
