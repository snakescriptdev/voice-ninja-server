import re
import requests
from app_v2.core.logger import setup_logger
from fastapi import HTTPException

logger = setup_logger(__name__)

def scrape_webpage_title(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; KnowledgeBot/1.0)"
            }
        )
        response.raise_for_status()

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else 400

        if status_code == 403:
            raise HTTPException(
                status_code=403,
                detail="Access to this URL is forbidden (403)."
            )
        elif status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="URL not found (404)."
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch URL. HTTP {status_code}."
            )

    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=408,
            detail="Request to URL timed out."
        )

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=400,
            detail="Invalid or inaccessible URL."
        )

    html = response.text

    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        html,
        re.IGNORECASE | re.DOTALL
    )

    if match:
        return match.group(1).strip()

    return "No title found"




