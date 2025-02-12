from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ErrorResponse(BaseModel):
    error: str

class SuccessResponse(BaseModel):
    message: str