"""
Base ElevenLabs Class

This module provides a base class for all ElevenLabs API interactions using HTTP requests.
"""

import requests
import logging
from typing import Dict, Any, Optional
from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY, BASE_URL
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


class ElevenLabsResponse:
    """
    Standard response wrapper for ElevenLabs API calls.
    """
    def __init__(self, status: bool, data: Optional[Any] = None, error_message: str = ""):
        self.status = status
        self.data = data
        self.error_message = error_message

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary format."""
        return {
            "status": self.status,
            "data": self.data,
            "error_message": self.error_message
        }
    
    def __bool__(self):
        """Allow boolean evaluation of response."""
        return self.status


class BaseElevenLabs:
    """
    Base class for ElevenLabs API operations.
    Provides common HTTP methods and error handling.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize BaseElevenLabs with API key and base configuration.
        
        Args:
            api_key: ElevenLabs API key. If not provided, uses config default.
        """
        self.api_key = api_key or ELEVENLABS_API_KEY
        self.base_url = BASE_URL
        self.headers = {
            "xi-api-key": self.api_key
        }
        
        if not self.api_key:
            logger.warning("ElevenLabs API key not configured")
    
    def _get(
        self,
        endpoint: str,
        params: Optional[Dict] = None,
        retries: int = 3,
        raw: bool = False
    ) -> ElevenLabsResponse:
        """
        Make a GET request to ElevenLabs API.

        Args:
            endpoint: API endpoint (e.g., '/voices')
            params: Query parameters
            retries: Number of retry attempts
            raw: Return raw response bytes (for audio, etc.)

        Returns:
            ElevenLabsResponse object
        """
        url = f"{self.base_url}{endpoint}"
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"GET request to {url} (attempt {attempt}/{retries})")
                response = requests.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=30
                )

                if response.status_code == 200:
                    # ✅ RAW (audio / binary)
                    if raw:
                        return ElevenLabsResponse(
                            status=True,
                            data={
                                "content": response.content,
                                "content_type": response.headers.get(
                                    "content-type", "application/octet-stream"
                                )
                            }
                        )

                    # ✅ JSON (default)
                    return ElevenLabsResponse(
                        status=True,
                        data=response.json()
                    )

                last_error = f"Status {response.status_code}: {response.text}"
                logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")

            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")

            except Exception as e:
                last_error = str(e)
                logger.error(f"Unexpected error on attempt {attempt}/{retries}: {last_error}")

        return ElevenLabsResponse(status=False, error_message=last_error)

    
    def _post(self, endpoint: str, data: Optional[Dict] = None, files: Optional[Dict] = None, 
              retries: int = 3) -> ElevenLabsResponse:
        """
        Make a POST request to ElevenLabs API.
        
        Args:
            endpoint: API endpoint
            data: Request body data (JSON)
            files: Files to upload (for multipart/form-data)
            retries: Number of retry attempts
            
        Returns:
            ElevenLabsResponse object
        """
        url = f"{self.base_url}{endpoint}"
        last_error = None
        
        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"POST request to {url} (attempt {attempt}/{retries})")
                
                # Use different headers for file uploads
                headers = self.headers.copy()
                if files:
                    headers.pop("Content-Type", None)  # Let requests set it for multipart
                
                response = requests.post(
                    url, 
                    headers=headers, 
                    json=data if not files else None,
                    data=data if files else None,
                    files=files,
                    timeout=60
                )
                
                if response.status_code in [200, 201]:
                    return ElevenLabsResponse(status=True, data=response.json())
                else:
                    last_error = f"Status {response.status_code}: {response.text}"
                    logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")
                    
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.error(f"Unexpected error on attempt {attempt}/{retries}: {last_error}")
        
        return ElevenLabsResponse(status=False, error_message=last_error)
    
    def _patch(self, endpoint: str, data: Dict, retries: int = 3) -> ElevenLabsResponse:
        """
        Make a PATCH request to ElevenLabs API.
        
        Args:
            endpoint: API endpoint
            data: Request body data
            retries: Number of retry attempts
            
        Returns:
            ElevenLabsResponse object
        """
        url = f"{self.base_url}{endpoint}"
        last_error = None
        
        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"PATCH request to {url} (attempt {attempt}/{retries})")
                response = requests.patch(url, headers=self.headers, json=data, timeout=30)
                
                if response.status_code == 200:
                    return ElevenLabsResponse(status=True, data=response.json())
                else:
                    last_error = f"Status {response.status_code}: {response.text}"
                    logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")
                    
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.error(f"Unexpected error on attempt {attempt}/{retries}: {last_error}")
        
        return ElevenLabsResponse(status=False, error_message=last_error)
    
    def _delete(self, endpoint: str, retries: int = 3) -> ElevenLabsResponse:
        """
        Make a DELETE request to ElevenLabs API.
        
        Args:
            endpoint: API endpoint
            retries: Number of retry attempts
            
        Returns:
            ElevenLabsResponse object
        """
        url = f"{self.base_url}{endpoint}"
        last_error = None
        
        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"DELETE request to {url} (attempt {attempt}/{retries})")
                response = requests.delete(url, headers=self.headers, timeout=30)
                
                if response.status_code in [200, 204]:
                    # DELETE often returns 204 No Content
                    response_data = response.json() if response.status_code == 200 and response.text else {}
                    return ElevenLabsResponse(status=True, data=response_data)
                else:
                    last_error = f"Status {response.status_code}: {response.text}"
                    logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")
                    
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt}/{retries} failed - {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.error(f"Unexpected error on attempt {attempt}/{retries}: {last_error}")
        
        return ElevenLabsResponse(status=False, error_message=last_error)
