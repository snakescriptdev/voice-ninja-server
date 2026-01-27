from fastapi import Depends, HTTPException, status, APIRouter
from app_v2.dependecies import get_db
from app_v2.utils.jwt_utils import get_current_user

from sqlalchemy.orm import Session
from app_v2.databases.models.voices import VoiceModel

