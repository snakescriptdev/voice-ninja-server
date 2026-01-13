"""Pydantic schemas for Google OAuth authentication."""

from typing import Dict, Any
from pydantic import BaseModel, Field


class GoogleLoginResponse(BaseModel):
    """Response schema for Google login URL."""
    
    status: str = Field(..., description='Response status')
    status_code: int = Field(..., description='HTTP status code')
    message: str = Field(..., description='Response message')
    data: Dict[str, str] = Field(..., description='Response data containing authorization URL')
    
    class Config:
        json_schema_extra = {
            'example': {
                'status': 'success',
                'status_code': 200,
                'message': 'Google authorization URL generated',
                'data': {
                    'url': 'https://accounts.google.com/o/oauth2/auth?response_type=code&...'
                }
            }
        }


class GoogleCallbackResponse(BaseModel):
    """Response schema for Google OAuth callback."""
    
    status: str = Field(..., description='Response status')
    status_code: int = Field(..., description='HTTP status code')
    message: str = Field(..., description='Response message')
    data: Dict[str, Any] = Field(..., description='Response data with tokens and user info')
    
    class Config:
        json_schema_extra = {
            'example': {
                'status': 'success',
                'status_code': 200,
                'message': 'Login successful',
                'data': {
                    'access_token': 'eyJhbGci...',
                    'refresh_token': 'eyJhbGci...',
                    'id': 1,
                    'email': 'user@gmail.com',
                    'phone': '',
                    'role': 'user',
                    'is_new_user': False
                }
            }
        }
