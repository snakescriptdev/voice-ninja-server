"""USD -> INR conversion.

Fetches the live rate from Frankfurter (https://api.frankfurter.app), a free
exchange-rate API backed by European Central Bank reference rates — no API
key required. The rate is cached in-process for CACHE_TTL_SECONDS since it
only needs to be "current", not real-time, and this is called on every
LLM-pricing lookup.
"""

import time
from typing import Optional

import requests

from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.app/latest"
CACHE_TTL_SECONDS = 3600
# Used only if the live rate has never been fetched successfully yet.
FALLBACK_USD_TO_INR_RATE = 88.0

_cache: dict[str, Optional[float]] = {"rate": None}
_cache_fetched_at = 0.0


def get_usd_to_inr_rate() -> float:
    """Returns the current USD->INR rate, cached for CACHE_TTL_SECONDS."""
    global _cache_fetched_at

    now = time.time()
    if _cache["rate"] is not None and (now - _cache_fetched_at) < CACHE_TTL_SECONDS:
        return _cache["rate"]

    try:
        response = requests.get(
            FRANKFURTER_URL, params={"from": "USD", "to": "INR"}, timeout=10
        )
        response.raise_for_status()
        rate = float(response.json()["rates"]["INR"])
        _cache["rate"] = rate
        _cache_fetched_at = now
        return rate
    except Exception as e:
        logger.warning(f"Failed to fetch USD->INR rate, using cached/fallback: {e}")
        return _cache["rate"] or FALLBACK_USD_TO_INR_RATE
