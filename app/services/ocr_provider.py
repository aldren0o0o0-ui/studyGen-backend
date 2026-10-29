"""
services/ocr_provider.py — Phase 8 OCR Provider Abstraction
Supports Tesseract, PyMuPDF OCR, and Mock OCR providers for deterministic testing.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class OCRPageResult:
    text: str
    confidence: float
    ocr_engine: str
    page_number: int
    success: bool = True
    error_message: Optional[str] = None


class OCRProvider(ABC):
    """Abstract interface for page OCR engines."""

    @property
    @abstractmethod
    def engine_name(self) -> str:
        pass

    @abstractmethod
    def extract_page_image(self, image_input: Any, page_number: int) -> OCRPageResult:
        """
        Extract text from an in-memory PIL image or PyMuPDF pixmap object.
        """
        pass


class MockOCRProvider(OCRProvider):
    """
    Deterministic mock OCR provider for unit & integration testing.
    Can be seeded with preset page text responses.
    """

    def __init__(self, preset_text_map: Optional[dict[int, str]] = None, default_text: str = "OCR Extracted Text from Page Sample Content."):
        self.preset_text_map = preset_text_map or {}
        self.default_text = default_text

    @property
    def engine_name(self) -> str:
        return "mock_tesseract_5.3"

    def extract_page_image(self, image_input: Any, page_number: int) -> OCRPageResult:
        text = self.preset_text_map.get(page_number, f"{self.default_text} (Page {page_number})")
        if text.startswith("__FAIL__"):
            return OCRPageResult(
                text="",
                confidence=0.0,
                ocr_engine=self.engine_name,
                page_number=page_number,
                success=False,
                error_message=text.replace("__FAIL__", "").strip() or "Mock OCR processing failed",
            )
        return OCRPageResult(
            text=text,
            confidence=0.92,
            ocr_engine=self.engine_name,
            page_number=page_number,
            success=True,
        )


class PyTesseractOCRProvider(OCRProvider):
    """Local Tesseract OCR provider using pytesseract and PIL."""

    def __init__(self, tesseract_cmd: Optional[str] = None):
        self._tesseract_cmd = tesseract_cmd

    @property
    def engine_name(self) -> str:
        return "tesseract_pytesseract"

    def extract_page_image(self, image_input: Any, page_number: int) -> OCRPageResult:
        try:
            import pytesseract
            if self._tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd

            # Handle fitz Pixmap vs PIL Image vs bytes
            image = image_input
            if hasattr(image_input, "tobytes"):  # fitz.Pixmap
                from PIL import Image
                import io
                img_bytes = image_input.tobytes("png")
                image = Image.open(io.BytesIO(img_bytes))

            text = pytesseract.image_to_string(image, lang="eng").strip()
            return OCRPageResult(
                text=text,
                confidence=0.85 if text else 0.0,
                ocr_engine=self.engine_name,
                page_number=page_number,
                success=True,
            )
        except Exception as exc:
            logger.warning("PyTesseract OCR failed on page %d: %s", page_number, exc)
            return OCRPageResult(
                text="",
                confidence=0.0,
                ocr_engine=self.engine_name,
                page_number=page_number,
                success=False,
                error_message=str(exc),
            )


class PyMuPDFOCRProvider(OCRProvider):
    """PyMuPDF built-in OCR provider using get_textpage_ocr()."""

    def __init__(self, fitz_page: Any = None):
        self.fitz_page = fitz_page

    @property
    def engine_name(self) -> str:
        return "pymupdf_tesseract"

    def extract_page_image(self, image_input: Any, page_number: int) -> OCRPageResult:
        try:
            page = image_input if hasattr(image_input, "get_textpage_ocr") else self.fitz_page
            if page is None or not hasattr(page, "get_textpage_ocr"):
                raise ValueError("PyMuPDF page instance required for fitz OCR.")
            tp = page.get_textpage_ocr(language="eng", dpi=150)
            text = tp.extractText().strip()
            return OCRPageResult(
                text=text,
                confidence=0.85 if text else 0.0,
                ocr_engine=self.engine_name,
                page_number=page_number,
                success=True,
            )
        except Exception as exc:
            logger.warning("PyMuPDF OCR failed on page %d: %s", page_number, exc)
            return OCRPageResult(
                text="",
                confidence=0.0,
                ocr_engine=self.engine_name,
                page_number=page_number,
                success=False,
                error_message=str(exc),
            )


def get_ocr_provider(forced_provider: Optional[OCRProvider] = None) -> OCRProvider:
    """Factory returning active or mock OCR provider."""
    if forced_provider is not None:
        return forced_provider
    try:
        import pytesseract
        return PyTesseractOCRProvider()
    except ImportError:
        return MockOCRProvider()
