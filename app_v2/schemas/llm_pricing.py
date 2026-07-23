from typing import List

from pydantic import BaseModel


class LlmPriceItem(BaseModel):
    llm: str
    price_per_minute_usd: float
    price_per_minute_inr: float


class LlmPricingResponse(BaseModel):
    llm_prices: List[LlmPriceItem]
    usd_to_inr_rate: float
