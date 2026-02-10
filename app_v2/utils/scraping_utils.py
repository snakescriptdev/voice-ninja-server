import re
import requests
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

def scrape_webpage_title(url: str) -> str:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching the URL: {e}")
        raise 

    html = response.text

    # Regex to capture content inside <title>...</title>
    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        html,
        re.IGNORECASE | re.DOTALL
    )

    if match:
        # Clean extra whitespace and newlines
        return match.group(1).strip()

    return "No title found"




