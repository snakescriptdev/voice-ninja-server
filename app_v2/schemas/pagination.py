from fastapi import Query
from pydantic import BaseModel, BeforeValidator
from typing import Generic, TypeVar, List, Literal, Annotated

T = TypeVar("T")


def _coerce_page_size(value):
    # Query params always arrive as strings — Pydantic v2's Literal[int, ...]
    # does NOT coerce "10" -> 10 on its own (it requires an exact type+value
    # match), so without this every request would fail validation regardless
    # of the size value sent.
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value


# Allowed "items per page" values accepted across all list endpoints, default 10.
# The Query() (with no default) lives inside the Annotated itself — FastAPI
# requires the actual default to be set via a plain `= 10` at each usage site
# (`size: PageSize = 10`), not via `Query(10)` there, or it raises "`Query`
# default value cannot be set in `Annotated`" at startup.
PageSize = Annotated[Literal[10, 20, 50], BeforeValidator(_coerce_page_size), Query()]

class PaginatedResponse(BaseModel, Generic[T]):
    total: int
    page: int
    size: int
    pages: int
    items: List[T]


# Public API (app_v2/routers/public_api.py) only — kept separate from
# PaginatedResponse because that one is shared with internal/admin routers
# whose consumers (the frontend) already depend on the `page`/`pages` keys.
class PublicPaginatedResponse(BaseModel, Generic[T]):
    total: int
    current_page: int
    size: int
    total_pages: int
    has_next: bool
    has_previous: bool
    items: List[T]
