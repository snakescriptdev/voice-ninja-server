"""
Local text extraction for personal knowledge base file uploads (.pdf, .docx,
.txt). Runs entirely in-process — needed now that files are embedded locally
via pgvector instead of being handed to ElevenLabs for parsing.
"""

import os
import docx2txt
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)


def _extract_pdf_text(file_path: str) -> str:
    import fitz  # pymupdf

    text_parts = []
    with fitz.open(file_path) as doc:
        for page in doc:
            page_text = page.get_text().strip()
            if not page_text:
                # Scanned/image-only page — fall back to OCR.
                page_text = _ocr_pdf_page(page)
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


def _ocr_pdf_page(page) -> str:
    try:
        import pytesseract
        from PIL import Image
        import io

        pixmap = page.get_pixmap(dpi=200)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        return pytesseract.image_to_string(image).strip()
    except Exception as e:
        logger.warning(f"OCR fallback failed for a PDF page: {e}")
        return ""


def _extract_docx_text(file_path: str) -> str:
    return docx2txt.process(file_path).strip()


def _extract_txt_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()


def extract_text_from_file(file_path: str) -> str:
    """Extract plain text from a local .pdf, .docx, or .txt file."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = _extract_pdf_text(file_path)
    elif ext == ".docx":
        text = _extract_docx_text(file_path)
    elif ext == ".txt":
        text = _extract_txt_text(file_path)
    else:
        raise ValueError(f"Unsupported file type for text extraction: {ext}")

    if not text:
        raise ValueError("No readable text could be extracted from this file.")
    return text
