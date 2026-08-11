"""PDF rendering and text extraction.

Per page we:
  1. render the page to a PNG (for the viewer and for vision extraction),
  2. try the embedded text layer (vector CAD exports),
  3. fall back to OCR when the text layer is missing/sparse (scanned prints).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import fitz  # PyMuPDF

from .config import settings


@dataclass
class PageResult:
    page_number: int
    image_path: str
    width: int
    height: int
    text: str
    has_text_layer: bool
    used_ocr: bool


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ocr_image(image_path: str) -> str:
    """OCR a rendered page image. Tesseract is optional; failures degrade
    gracefully to an empty string so ingestion never crashes on OCR."""
    try:
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(image_path))
    except Exception:  # noqa: BLE001 - OCR is best-effort
        return ""


def process_pdf(pdf_path: str, out_dir: str) -> list[PageResult]:
    """Render every page to `out_dir` and extract text. Returns per-page results."""
    os.makedirs(out_dir, exist_ok=True)
    results: list[PageResult] = []
    zoom = settings.render_dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix)
            image_path = os.path.join(out_dir, f"page-{i:04d}.png")
            pix.save(image_path)

            text = (page.get_text() or "").strip()
            has_text_layer = len(text) >= settings.text_layer_min_chars
            used_ocr = False
            if not has_text_layer:
                ocr_text = _ocr_image(image_path)
                if ocr_text.strip():
                    text = ocr_text.strip()
                    used_ocr = True

            results.append(
                PageResult(
                    page_number=i,
                    image_path=image_path,
                    width=pix.width,
                    height=pix.height,
                    text=text,
                    has_text_layer=has_text_layer,
                    used_ocr=used_ocr,
                )
            )
    return results


def load_page_image(image_path: str) -> bytes:
    with open(image_path, "rb") as f:
        return f.read()
