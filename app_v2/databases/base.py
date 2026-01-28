from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

class Base(DeclarativeBase):
    pass





class TimeStampMixin:

    created_at: Mapped[datetime] = mapped_column(DateTime,default= datetime.utcnow,nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow,nullable=False,onupdate=datetime.now)
    