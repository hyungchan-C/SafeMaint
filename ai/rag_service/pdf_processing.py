from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import fitz

from preprocessing.pdf_pipeline import process_pdf


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


def _inspect_pdf(path: Path) -> tuple[int, bool]:
    """Return page count and whether a raster image exists in the PDF."""
    with fitz.open(path) as pdf:
        return pdf.page_count, any(page.get_images(full=True) for page in pdf)


def _parse_page_range(value: object) -> tuple[int | None, int | None]:
    if isinstance(value, int):
        return (value, value) if value >= 1 else (None, None)
    if not isinstance(value, str):
        return None, None
    parts = value.strip().split("-", maxsplit=1)
    try:
        start = int(parts[0])
        end = int(parts[-1])
    except (TypeError, ValueError):
        return None, None
    if start < 1 or end < start:
        return None, None
    return start, end


def process_document_pdf(
    pdf_path: str | Path,
    *,
    title: str,
    manufacturer: str = "",
    product_type: str = "",
    model_name: str = "",
    chunk_size: int = 1200,
    overlap: int = 150,
) -> ProcessedPdf:
    """Normalize the shared PDF pipeline output for database storage.

    Docling remains optional. The shared pipeline falls back to PyMuPDF when
    Docling is not installed; image-only files are explicitly classified so
    the worker never invents text for a scanned manual.
    """
    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Stored PDF does not exist: {path}")

    page_count, has_raster_images = _inspect_pdf(path)
    if page_count == 0:
        return ProcessedPdf(kind="empty", page_count=0, chunks=())

    raw_chunks = process_pdf(
        str(path),
        product_type=product_type,
        model_name=model_name,
        manufacturer=manufacturer,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    normalized: list[ProcessedChunk] = []
    for raw_chunk in raw_chunks:
        content = str(raw_chunk.get("text", "")).strip()
        if not content:
            continue
        source_chunk_id = str(raw_chunk.get("chunk_id", "")).strip()
        if not source_chunk_id:
            source_chunk_id = hashlib.sha256(content.encode("utf-8")).hexdigest()
        pipeline_metadata = dict(raw_chunk.get("metadata") or {})
        page_start, page_end = _parse_page_range(pipeline_metadata.get("페이지"))
        section = str(pipeline_metadata.get("챕터", "")).strip()
        metadata = {
            **pipeline_metadata,
            "document_title": title,
            "manufacturer": manufacturer,
            "product_type": product_type,
            "model_name": model_name,
            "section": section or None,
            "page_start": page_start,
            "page_end": page_end,
            "source_chunk_id": source_chunk_id,
        }
        normalized.append(
            ProcessedChunk(
                source_chunk_id=source_chunk_id,
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                page_start=page_start,
                page_end=page_end,
                section_path=(section,) if section else (),
                metadata=metadata,
            )
        )

    if normalized:
        return ProcessedPdf(kind="text", page_count=page_count, chunks=tuple(normalized))
    return ProcessedPdf(
        kind="scanned" if has_raster_images else "empty",
        page_count=page_count,
        chunks=(),
    )
