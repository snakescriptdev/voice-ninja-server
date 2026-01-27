from app_v2.databases.base import Base
from app_v2.databases.session import engine

# import models here 
import app_v2.databases.models





def init__db():
    Base.metadata.create_all(bind=engine)