"""In-memory PDF inspection and selective text/render routing using PyMuPDF."""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import pymupdf


@dataclass
class PdfPagePayload:
    """Extracted text or rendered PNG image payload for a single PDF page."""

    page_number: int
    has_embedded_text: bool
    embedded_text: Optional[str]
    rendered_png_bytes: Optional[bytes]
    width_px: int
    height_px: int


def inspect_and_extract_pdf(
    pdf_bytes: bytes,
    min_text_chars: int = 40,
    render_dpi: int = 250,
    max_pages: int = 5,
) -> Tuple[int, List[PdfPagePayload]]:
    """Inspect PDF bytes in memory and extract text or render PNGs for OCR.

    Pages containing at least ``min_text_chars`` of non-whitespace embedded text
    are extracted directly without rasterization. Scanned or image-only pages
    are rendered at ``render_dpi`` into PNG bytes strictly in memory.

    Args:
        pdf_bytes: Raw PDF document bytes.
        min_text_chars: Minimum stripped character count to treat a page as
            having selectable embedded text.
        render_dpi: Rasterization resolution for scanned pages.
        max_pages: Maximum number of leading pages to process.

    Returns:
        Tuple of ``(total_page_count, page_payloads)``.
    """
    if not pdf_bytes:
        raise ValueError("PDF payload is empty.")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1.")
    if render_dpi < 72:
        raise ValueError("render_dpi must be at least 72.")

    pages: List[PdfPagePayload] = []
    scale = render_dpi / 72.0

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        total_pages = len(doc)
        if total_pages == 0:
            raise ValueError("PDF document contains no pages.")

        pages_to_process = min(total_pages, max_pages)
        for page_index in range(pages_to_process):
            page = doc[page_index]
            extracted_text = (page.get_text("text") or "").strip()

            if len(extracted_text) >= min_text_chars:
                width_px = max(1, int(round(page.rect.width * scale)))
                height_px = max(1, int(round(page.rect.height * scale)))
                pages.append(
                    PdfPagePayload(
                        page_number=page_index + 1,
                        has_embedded_text=True,
                        embedded_text=extracted_text,
                        rendered_png_bytes=None,
                        width_px=width_px,
                        height_px=height_px,
                    )
                )
            else:
                pixmap = page.get_pixmap(dpi=render_dpi, alpha=False)
                pages.append(
                    PdfPagePayload(
                        page_number=page_index + 1,
                        has_embedded_text=False,
                        embedded_text=None,
                        rendered_png_bytes=pixmap.tobytes("png"),
                        width_px=int(pixmap.width),
                        height_px=int(pixmap.height),
                    )
                )

    return total_pages, pages
