"""
ElevenLabs Knowledge Base Utilities

This module provides utilities for Knowledge Base (KB) operations with the ElevenLabs API.
Handles document uploading, URL addition, processing status, and deletion.
"""

import os
import re
import json
import mimetypes
from typing import Optional, Dict, Any, List
from .base import BaseElevenLabs, ElevenLabsResponse
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

# Known ElevenLabs KB-sync failure statuses mapped to a short, actionable
# message a user can actually do something with. Anything not in this map
# falls back to a generic message — the raw ElevenLabs body (with its
# request_id/stack-trace-like detail) is for logs only, never for users.
_FRIENDLY_KB_SYNC_ERRORS = {
    "ReadabilityError": (
        "We couldn't extract readable content from this page. Make sure the "
        "URL is publicly accessible and contains real text content — not a "
        "login-gated page, a bare PDF/file link, or a page rendered entirely "
        "by JavaScript."
    ),
}


def describe_kb_sync_error(raw_error_message: Optional[str]) -> str:
    """
    Translate a raw ElevenLabs error (typically `Status <code>: <json body>`)
    into a short, user-facing message. The raw body is logged by the caller
    before this is used — this function only decides what the user sees.
    """
    match = re.search(r"\{.*\}", raw_error_message or "", re.DOTALL)
    if match:
        try:
            body = json.loads(match.group(0))
            detail = body.get("detail") if isinstance(body, dict) else None
            error_status = detail.get("status") if isinstance(detail, dict) else None
            if error_status in _FRIENDLY_KB_SYNC_ERRORS:
                return _FRIENDLY_KB_SYNC_ERRORS[error_status]
        except (ValueError, AttributeError, TypeError):
            pass
    return (
        "We couldn't add this URL to your knowledge base right now. Please "
        "double-check the link and try again."
    )


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
    
    def compute_rag_index(self, document_id: str) -> Optional[str]:
        """
        Compute the RAG index for a document.
        
        Args:
            document_id: ElevenLabs document ID
            
        Returns:
            RAG index ID if successful, None otherwise
        """
        logger.info(f"Computing RAG index for document: {document_id}")
        # Send model parameter as required by ElevenLabs API
        payload = {
            "model": "e5_mistral_7b_instruct"
        }
        response = self._post(f"/convai/knowledge-base/{document_id}/rag-index", data=payload)
        
        if response.status and response.data:
            logger.info(f"✅ RAG index computed for document: {document_id}")
            return response.data.get("id")
        else:
            logger.error(f"Failed to compute RAG index for document: {response.error_message}")
            return None

    def get_agent_knowledge_base_size(self, agent_id: str) -> ElevenLabsResponse:
        """
        Fetch ElevenLabs' computed knowledge-base size (in pages) for all
        documents attached to this agent.

        Args:
            agent_id: ElevenLabs agent ID.

        Returns:
            ElevenLabsResponse whose data contains the page-count payload.
        """
        logger.info(f"Fetching knowledge base size for agent: {agent_id}")
        response = self._get(f"/convai/agent/{agent_id}/knowledge-base/size")

        if response.status:
            logger.info(f"✅ KB size fetched for agent {agent_id}")
        else:
            logger.error(f"Failed to fetch KB size for agent {agent_id}: {response.error_message}")

        return response

    @staticmethod
    def _extract_page_count(data: Dict[str, Any]) -> Optional[int]:
        """
        Best-effort pull of the page count out of the KB-size response — the
        exact key hasn't been confirmed against a live payload, so probe the
        plausible candidates (mirrors _extract_llm_credits in
        conversation_utils.py, which faced the same key-naming uncertainty).
        """
        if not isinstance(data, dict):
            return None
        for key in ("number_of_pages", "total_pages", "pages", "size"):
            value = data.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
        return None

    def get_kb_total_pages(self, agent_id: str) -> Optional[int]:
        """
        Convenience: total KB page count for an agent, or None on any failure.
        Best-effort — callers should never let this block agent create/update.
        """
        try:
            resp = self.get_agent_knowledge_base_size(agent_id)
            if not resp.status or not resp.data:
                return None
            return self._extract_page_count(resp.data)
        except Exception:
            logger.exception(f"Failed to resolve KB total pages for agent {agent_id}")
            return None

    @staticmethod
    def _extract_document_page_count(data: Dict[str, Any]) -> Optional[int]:
        """
        Best-effort pull of a single document's page count out of
        GET /convai/knowledge-base/{document_id} — the exact key hasn't been
        confirmed against a live payload, so probe the plausible candidates
        (mirrors _extract_page_count, which faced the same key-naming
        uncertainty for the agent-level size endpoint).
        """
        if not isinstance(data, dict):
            return None
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        for source in (metadata, data):
            for key in ("number_of_pages", "total_pages", "pages", "size"):
                value = source.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        continue
        return None

    def get_document_page_count(self, document_id: str) -> Optional[int]:
        """
        Convenience: page count for a single KB document, or None on any
        failure. Best-effort — callers should never let this block KB
        upload/update.
        """
        try:
            resp = self.get_document_status(document_id)
            if not resp.status or not resp.data:
                return None
            return self._extract_document_page_count(resp.data)
        except Exception:
            logger.exception(f"Failed to resolve page count for document {document_id}")
            return None

