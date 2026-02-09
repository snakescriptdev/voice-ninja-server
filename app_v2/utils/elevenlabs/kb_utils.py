"""
ElevenLabs Knowledge Base Utilities

This module provides utilities for Knowledge Base (KB) operations with the ElevenLabs API.
Handles document uploading, URL addition, processing status, and deletion.
"""

import os
import mimetypes
from typing import Optional, Dict, Any, List
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


class ElevenLabsKB(BaseElevenLabs):
    """
    Knowledge Base utility class for ElevenLabs API operations.
    Handles all document and knowledge-related API calls.
    """
    
    def upload_document(self, file_path: str, name: Optional[str] = None) -> ElevenLabsResponse:
        """
        Upload a local file to ElevenLabs Knowledge Base.
        
        Args:
            file_path: Path to the local file (PDF, DOCX, TXT)
            name: Optional name for the document in ElevenLabs
            
        Returns:
            ElevenLabsResponse with document_id and status
        """
        try:
            filename = name or os.path.basename(file_path)
            logger.info(f"Uploading document to ElevenLabs: {filename} from {file_path}")
            
            if not os.path.exists(file_path):
                return ElevenLabsResponse(status=False, error_message=f"File not found: {file_path}")
            
            # Guess mime type
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type:
                mime_type = "application/octet-stream"

            with open(file_path, "rb") as f:
                # Explicitly set filename and mime_type in the files tuple
                # dict structure: {"field_name": (filename, file_object, content_type)}
                files = {"file": (filename, f, mime_type)}
                data = {"name": filename}
                
                # Updated endpoint to standardized /knowledge-base
                response = self._post("/convai/knowledge-base", data=data, files=files)
                
                if response.status:
                    doc_id = response.data.get("id")
                    logger.info(f"✅ Document uploaded to ElevenLabs: {filename} (ID: {doc_id})")
                    return ElevenLabsResponse(status=True, data={"document_id": doc_id, "name": filename})
                else:
                    logger.error(f"Failed to upload document to ElevenLabs: {response.error_message}")
                    return response
                    
        except Exception as e:
            error_msg = f"Error uploading document: {str(e)}"
            logger.error(error_msg)
            return ElevenLabsResponse(status=False, error_message=error_msg)

    def add_url_document(self, url: str, name: Optional[str] = None) -> ElevenLabsResponse:
        """
        Add a URL to ElevenLabs Knowledge Base.
        
        Args:
            url: The URL to index
            name: Optional name for the document
            
        Returns:
            ElevenLabsResponse with document_id
        """
        logger.info(f"Adding URL to ElevenLabs KB: {url}")
        
        # Use multipart/form-data for URL addition as well
        # passing fields in 'files' with None filename forces multipart in requests
        files_payload = {
            "url": (None, url),
            "name": (None, name or url)
        }
        
        response = self._post("/convai/knowledge-base", files=files_payload)
        
        if response.status:
            doc_id = response.data.get("id")
            logger.info(f"✅ URL added to ElevenLabs KB (ID: {doc_id})")
            return ElevenLabsResponse(status=True, data={"document_id": doc_id})
        else:
            logger.error(f"Failed to add URL to ElevenLabs KB: {response.error_message}")
            return response

    def add_text_document(self, text: str, name: str) -> ElevenLabsResponse:
        """
        Add plain text to ElevenLabs Knowledge Base.
        
        Args:
            text: The text content to index
            name: Name for the document
            
        Returns:
            ElevenLabsResponse with document_id
        """
        logger.info(f"Adding text document to ElevenLabs KB: {name}")
        
        # Upload text as a file
        files = {
            "file": ("content.txt", text, "text/plain")
        }
        data = {
            "name": name
        }
        
        response = self._post("/convai/knowledge-base", data=data, files=files)
        
        if response.status:
            doc_id = response.data.get("id")
            logger.info(f"✅ Text document added to ElevenLabs KB: {name} (ID: {doc_id})")
            return ElevenLabsResponse(status=True, data={"document_id": doc_id})
        else:
            logger.error(f"Failed to add text document to ElevenLabs KB: {response.error_message}")
            return response

    def delete_document(self, document_id: str) -> ElevenLabsResponse:
        """
        Delete a document from ElevenLabs Knowledge Base.
        
        Args:
            document_id: ElevenLabs document ID
            
        Returns:
            ElevenLabsResponse
        """
        logger.info(f"Deleting document from ElevenLabs KB: {document_id}")
        
        response = self._delete(f"/convai/knowledge-base/{document_id}")
        
        if response.status:
            logger.info(f"✅ Document deleted from ElevenLabs KB: {document_id}")
        else:
            logger.error(f"Failed to delete document from ElevenLabs KB: {response.error_message}")
            
        return response

    def update_document_name(self, document_id: str, name: str) -> ElevenLabsResponse:
        """
        Update the name of a document in ElevenLabs Knowledge Base.
        """
        logger.info(f"Updating document name in ElevenLabs KB: {document_id} -> {name}")
        data = {"name": name}
        response = self._patch(f"/convai/knowledge-base/{document_id}", data=data)
        if response.status:
            logger.info(f"✅ Document name updated in ElevenLabs KB: {document_id}")
        else:
            logger.error(f"Failed to update document name in ElevenLabs KB: {response.error_message}")
        return response

    def get_document_status(self, document_id: str) -> ElevenLabsResponse:
        """
        Check the processing status of a document.
        
        Args:
            document_id: ElevenLabs document ID
            
        Returns:
            ElevenLabsResponse with status details
        """
        response = self._get(f"/convai/knowledge-base/{document_id}")
        return response
