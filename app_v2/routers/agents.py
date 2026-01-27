"""
    THis module defines the CRUD Routes for agent.
    it has following routes:
"""


from fastapi import APIRouter, Depends, HTTPException,status
from app_v2.utils.jwt_utils import get_current_user


