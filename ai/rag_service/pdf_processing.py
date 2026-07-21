from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import fitz

from preprocessing.pdf_pipeline import DoclingRuntimeSettings, process_pdf


PdfKind = Literal["text", "scanned", "empty"]


@dataclass(frozen=True, slots=True)
class ProcessedChunk:
    source_chunk_id: str
    content: str
    content_hash: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProcessedPdf:
    kind: PdfKind
    page_count: int
    chunks: tuple[ProcessedChunk, ...]
    processing_metadata: dict[str, Any]


def _inspect_pdf(path: Path) -> tuple[int, bool]:
    """Return page count and whether a raster image exists in the PDF."""
    with fitz.open(path) as pdf:
        return pdf.page_count, any(page.get_images(full=True) for page in pdf)


def process_document_pdf(
    pdf_path: str | Path,
    *,
    title: str,
    manufacturer: str = "",
    product_type: str = "",
    model_name: str = "",
    chunk_size: int = 1200,
    overlap: int = 150,
    docling_settings: DoclingRuntimeSettings | None = None,
    log_context: dict[str, Any] | None = None,
) -> ProcessedPdf:
    """Normalize the shared PDF pipeline output for database storage.

    Docling deployment errors are fatal by default. PyMuPDF fallback is only
    used for document-specific conversion failures when explicitly allowed;
    image-only files are classified so the worker never invents text.
    """
    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Stored PDF does not exist: {path}")

    page_count, has_raster_images = _inspect_pdf(path)
    if page_count == 0:
        return ProcessedPdf(
            kind="empty",
            page_count=0,
            chunks=(),
            processing_metadata={
                "extractor": "none",
                "extractor_version": None,
                "fallback_used": False,
                "fallback_reason": "PDF has zero pages.",
                "ocr_used": False,
            },
        )

    pipeline_result = process_pdf(
        str(path),
        product_type=product_type,
        model_name=model_name,
        manufacturer=manufacturer,
        chunk_size=chunk_size,
        overlap=overlap,
        docling_settings=docling_settings,
        log_context=log_context,
    )
    processing_metadata = dict(pipeline_result.get("processing_metadata") or {})
    normalized: list[ProcessedChunk] = []
    for raw_chunk in pipeline_result["chunks"]:
        content = str(raw_chunk.get("content", "")).strip()
        if not content:
            continue
        source_chunk_id = (
            f"{raw_chunk.get('document_external_id', '')}:{raw_chunk.get('chunk_index', '')}"
        )
        page_start = raw_chunk.get("page_start", raw_chunk.get("page_number"))
        page_end = raw_chunk.get("page_end", raw_chunk.get("page_number"))
        section_path = tuple(raw_chunk.get("section_path") or ())
        pipeline_metadata = dict(raw_chunk.get("metadata") or {})
        metadata = {
            **pipeline_metadata,
            "document_title": title,
            "manufacturer": manufacturer,
            "product_type": product_type,
            "model_name": model_name,
            "section": section_path[-1] if section_path else None,
            "page_start": page_start,
            "page_end": page_end,
            "source_chunk_id": source_chunk_id,
        }
        content_hash = raw_chunk.get("content_hash") or hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()
        normalized.append(
            ProcessedChunk(
                source_chunk_id=source_chunk_id,
                content=content,
                content_hash=content_hash,
                page_start=page_start,
                page_end=page_end,
                section_path=section_path,
                metadata=metadata,
            )
        )

    if normalized:
        return ProcessedPdf(
            kind="text",
            page_count=page_count,
            chunks=tuple(normalized),
            processing_metadata=processing_metadata,
        )
    return ProcessedPdf(
        kind="scanned" if has_raster_images else "empty",
        page_count=page_count,
        chunks=(),
        processing_metadata=processing_metadata,
    )
