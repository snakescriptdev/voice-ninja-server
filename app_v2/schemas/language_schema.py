from pydantic import BaseModel, Field
from typing import Optional

class LanguageBase(BaseModel):
    lang_code: str = Field(...,description="code for the language like en (for English)",min_length=2,max_length=10)
    language: str = Field(...,description="name of the langauge",min_length=3)


class LanguageIn(LanguageBase):
    pass

class LanguageUpdate(BaseModel):
    lang_code: Optional[str] = None
    language: Optional[str] = None


class LanguageRead(LanguageBase):
    id: int 

    class Config:
        from_attributes = True
