from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk


SUPPORTED_SOURCES = ("domestic", "fatal")
NULL_TEXT_VALUES = {"", "null", "none", "nan", "n/a"}


class IngestionError(RuntimeError):
    """Raised when source data cannot be imported without losing integrity."""


@dataclass(frozen=True, slots=True)
class SourceDocument:
    document_id: int
    source_type: str
    source_row_id: int
    title: str
    body_text: str | None
    search_text: str | None

    @property
    def external_id(self) -> str:
        return f"incident:{self.source_type}:{self.source_row_id}"

    @property
    def content_quality(self) -> str:
        return "full" if self.body_text else "title_only"


@dataclass(slots=True)
class ScanResult:
    data_dir: Path
    sources: tuple[str, ...]
    limit_per_source: int
    read_documents: int = 0
    read_chunks: int = 0
    source_document_counts: Counter[str] = field(default_factory=Counter)
    selected_source_counts: Counter[str] = field(default_factory=Counter)
    selected_documents: list[SourceDocument] = field(default_factory=list)
    selected_chunk_count: int = 0
    empty_title_count: int = 0
    empty_chunk_count: int = 0
    duplicate_external_id_count: int = 0
    duplicate_document_id_count: int = 0
    duplicate_chunk_index_count: int = 0
    orphan_chunk_count: int = 0
    title_only_count: int = 0
    missing_source_metadata_count: int = 0
    documents_without_chunks: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw_metadata: dict[tuple[str, int], dict[str, Any]] = field(
        default_factory=dict
    )

    @property
    def selected_document_ids(self) -> set[int]:
        return {document.document_id for document in self.selected_documents}

    @property
    def selected_by_document_id(self) -> dict[int, SourceDocument]:
        return {
            document.document_id: document for document in self.selected_documents
        }

    def to_report(self) -> dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "sources": list(self.sources),
            "limit_per_source": self.limit_per_source,
            "read_documents": self.read_documents,
            "read_chunks": self.read_chunks,
            "source_type_document_counts": dict(self.source_document_counts),
            "selected_source_counts": dict(self.selected_source_counts),
            "empty_title_count": self.empty_title_count,
            "empty_chunk_count": self.empty_chunk_count,
            "duplicate_external_id_count": self.duplicate_external_id_count,
            "duplicate_document_id_count": self.duplicate_document_id_count,
            "duplicate_chunk_index_count": self.duplicate_chunk_index_count,
            "orphan_document_id_count": self.orphan_chunk_count,
            "title_only_document_count": self.title_only_count,
            "missing_source_metadata_count": self.missing_source_metadata_count,
            "documents_without_chunks": self.documents_without_chunks,
            "planned_documents": len(self.selected_documents),
            "planned_chunks": self.selected_chunk_count,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass(slots=True)
class ImportResult:
    documents_inserted: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    chunks_inserted: int = 0
    chunks_updated: int = 0
    chunks_unchanged: int = 0
    committed_document_batches: int = 0
    committed_chunk_batches: int = 0

    def to_report(self) -> dict[str, int]:
        return asdict(self)


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized.lower() in NULL_TEXT_VALUES:
        return None
    return normalized


def normalize_chunk_content(value: Any) -> str:
    return normalize_optional_text(value) or ""


def content_sha256(content: str) -> str:
    normalized = normalize_chunk_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stable_external_id(source_type: str, source_row_id: int) -> str:
    if source_type not in SUPPORTED_SOURCES:
        raise ValueError(f"Unsupported source type: {source_type}")
    return f"incident:{source_type}:{source_row_id}"


def _required_int(record: dict[str, Any], key: str, context: str) -> int:
    value = record.get(key)
    if isinstance(value, bool):
        raise IngestionError(f"{context}: {key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise IngestionError(f"{context}: invalid {key}={value!r}") from exc


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise IngestionError(f"Cannot open JSONL file: {path}") from exc

    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise IngestionError(
                    f"{path.name}:{line_number}: invalid JSON ({exc.msg})"
                ) from exc
            if not isinstance(record, dict):
                raise IngestionError(
                    f"{path.name}:{line_number}: expected a JSON object"
                )
            yield line_number, record


def _parse_unambiguous_date(value: Any) -> date | None:
    text_value = normalize_optional_text(value)
    if not text_value:
        return None
    match = re.fullmatch(
        r"(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*(?:일|[.]?)",
        text_value,
    )
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _load_selected_raw_metadata(scan: ScanResult) -> None:
    selected_rows: dict[str, set[int]] = {source: set() for source in scan.sources}
    for document in scan.selected_documents:
        selected_rows[document.source_type].add(document.source_row_id)

    source_files = {
        "domestic": "domestic_cases.jsonl",
        "fatal": "fatal_cases.jsonl",
    }
    for source_type in scan.sources:
        wanted = selected_rows[source_type]
        if not wanted:
            continue
        for line_number, record in iter_jsonl(scan.data_dir / source_files[source_type]):
            source_row_id = _required_int(
                record,
                "id",
                f"{source_files[source_type]}:{line_number}",
            )
            if source_row_id not in wanted:
                continue
            scan.raw_metadata[(source_type, source_row_id)] = record

    for document in scan.selected_documents:
        if (document.source_type, document.source_row_id) not in scan.raw_metadata:
            scan.missing_source_metadata_count += 1
            scan.warnings.append(
                f"Missing {document.source_type} metadata for source_row_id="
                f"{document.source_row_id}"
            )


def scan_accident_dataset(
    data_dir: str | Path,
    sources: Sequence[str] = SUPPORTED_SOURCES,
    limit_per_source: int = 100,
) -> ScanResult:
    root = Path(data_dir).resolve()
    normalized_sources = tuple(dict.fromkeys(sources))
    invalid_sources = set(normalized_sources) - set(SUPPORTED_SOURCES)
    if invalid_sources:
        raise ValueError(f"Unsupported sources: {sorted(invalid_sources)}")
    if not normalized_sources:
        raise ValueError("At least one source must be selected")
    if limit_per_source <= 0:
        raise ValueError("limit_per_source must be greater than zero")

    required_files = (
        "accident_documents.jsonl",
        "accident_chunks.jsonl",
        *(("domestic_cases.jsonl",) if "domestic" in normalized_sources else ()),
        *(("fatal_cases.jsonl",) if "fatal" in normalized_sources else ()),
    )
    missing_files = [name for name in required_files if not (root / name).is_file()]
    if missing_files:
        raise IngestionError(f"Missing data files: {', '.join(missing_files)}")

    scan = ScanResult(
        data_dir=root,
        sources=normalized_sources,
        limit_per_source=limit_per_source,
    )
    all_document_ids: set[int] = set()
    seen_external_ids: set[str] = set()
    selected_counts: Counter[str] = Counter()

    documents_path = root / "accident_documents.jsonl"
    for line_number, record in iter_jsonl(documents_path):
        context = f"{documents_path.name}:{line_number}"
        source_type = normalize_optional_text(record.get("source_type")) or ""
        document_id = _required_int(record, "document_id", context)
        source_row_id = _required_int(record, "source_row_id", context)

        scan.read_documents += 1
        scan.source_document_counts[source_type] += 1
        if document_id in all_document_ids:
            scan.duplicate_document_id_count += 1
        all_document_ids.add(document_id)

        if source_type not in normalized_sources:
            continue
        external_id = stable_external_id(source_type, source_row_id)
        if external_id in seen_external_ids:
            scan.duplicate_external_id_count += 1
        seen_external_ids.add(external_id)

        if selected_counts[source_type] >= limit_per_source:
            continue
        title = normalize_optional_text(record.get("title")) or ""
        document = SourceDocument(
            document_id=document_id,
            source_type=source_type,
            source_row_id=source_row_id,
            title=title,
            body_text=normalize_optional_text(record.get("body_text")),
            search_text=normalize_optional_text(record.get("search_text")),
        )
        scan.selected_documents.append(document)
        selected_counts[source_type] += 1
        scan.selected_source_counts[source_type] += 1
        if not title:
            scan.empty_title_count += 1
        if document.content_quality == "title_only":
            scan.title_only_count += 1

    _load_selected_raw_metadata(scan)

    selected_ids = scan.selected_document_ids
    selected_chunk_documents: set[int] = set()
    seen_chunk_indexes: set[tuple[int, int]] = set()
    chunks_path = root / "accident_chunks.jsonl"
    for line_number, record in iter_jsonl(chunks_path):
        context = f"{chunks_path.name}:{line_number}"
        document_id = _required_int(record, "document_id", context)
        chunk_index = _required_int(record, "chunk_order", context)
        scan.read_chunks += 1

        if document_id not in all_document_ids:
            scan.orphan_chunk_count += 1
        chunk_key = (document_id, chunk_index)
        if chunk_key in seen_chunk_indexes:
            scan.duplicate_chunk_index_count += 1
        seen_chunk_indexes.add(chunk_key)

        if document_id not in selected_ids:
            continue
        scan.selected_chunk_count += 1
        selected_chunk_documents.add(document_id)
        if not normalize_chunk_content(record.get("chunk_text")):
            scan.empty_chunk_count += 1

    scan.documents_without_chunks = len(selected_ids - selected_chunk_documents)
    if scan.empty_title_count:
        scan.errors.append(
            f"Selected data contains {scan.empty_title_count} empty document titles"
        )
    if scan.empty_chunk_count:
        scan.errors.append(
            f"Selected data contains {scan.empty_chunk_count} empty chunks"
        )
    if scan.duplicate_external_id_count:
        scan.errors.append(
            f"Data contains {scan.duplicate_external_id_count} duplicate external IDs"
        )
    if scan.duplicate_document_id_count:
        scan.errors.append(
            f"Data contains {scan.duplicate_document_id_count} duplicate document IDs"
        )
    if scan.duplicate_chunk_index_count:
        scan.errors.append(
            f"Data contains {scan.duplicate_chunk_index_count} duplicate chunk indexes"
        )
    if scan.orphan_chunk_count:
        scan.errors.append(
            f"Data contains {scan.orphan_chunk_count} chunks with unknown document IDs"
        )
    return scan


def _metadata_for_document(
    document: SourceDocument,
    raw_metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any], date | None]:
    raw = raw_metadata or {}
    metadata: dict[str, Any] = {
        "dataset_type": document.source_type,
        "source_document_id": document.document_id,
        "source_row_id": document.source_row_id,
        "content_quality": document.content_quality,
    }
    published_at = None
    if document.source_type == "domestic":
        for key in ("boardno", "business", "detailed_business", "causal_object"):
            value = normalize_optional_text(raw.get(key))
            if value is not None:
                metadata[key] = value
    else:
        accident_date_text = normalize_optional_text(raw.get("accident_date_text"))
        location = normalize_optional_text(raw.get("location"))
        if accident_date_text is not None:
            metadata["accident_date_text"] = accident_date_text
            published_at = _parse_unambiguous_date(accident_date_text)
        if location is not None:
            metadata["location"] = location
    return metadata, published_at


def document_values(
    document: SourceDocument,
    raw_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata, published_at = _metadata_for_document(document, raw_metadata)
    return {
        "external_id": document.external_id,
        "title": document.title,
        "source_type": "incident",
        "document_type_code": "public_incident",
        "publisher": None,
        "source_url": None,
        "revision": None,
        "published_at": published_at,
        "access_level": "restricted",
        "file_sha256": None,
        "metadata": metadata,
    }


def chunk_values(
    record: dict[str, Any],
    document: SourceDocument,
    database_document_id: UUID,
) -> dict[str, Any]:
    content = normalize_chunk_content(record.get("chunk_text"))
    if not content:
        raise IngestionError(
            f"Empty chunk for source_document_id={document.document_id}, "
            f"chunk_order={record.get('chunk_order')!r}"
        )
    return {
        "document_id": database_document_id,
        "chunk_index": _required_int(record, "chunk_order", "accident chunk"),
        "page_number": None,
        "page_start": None,
        "page_end": None,
        "section_path": [],
        "content": content,
        "content_hash": content_sha256(content),
        "metadata": {
            "source_chunk_id": record.get("chunk_id"),
            "dataset_type": document.source_type,
            "source_document_id": document.document_id,
        },
        "embedding": None,
        "embedding_model": None,
        "embedding_dimension": None,
        "embedding_status": "pending",
    }


def assert_isolated_test_database(database_url: str) -> str:
    database_name = make_url(database_url).database or ""
    if not database_name.endswith("_test"):
        raise IngestionError(
            "Accident ingestion is restricted to a database ending in '_test'; "
            f"refusing database {database_name!r}."
        )
    return database_name


def _chunks(items: Sequence[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _document_changed(existing: Document, values: dict[str, Any]) -> bool:
    comparisons = {
        "title": values["title"],
        "source_type": values["source_type"],
        "document_type_code": values["document_type_code"],
        "publisher": values["publisher"],
        "source_url": values["source_url"],
        "revision": values["revision"],
        "published_at": values["published_at"],
        "access_level": values["access_level"],
        "file_sha256": values["file_sha256"],
        "metadata_json": values["metadata"],
    }
    return any(getattr(existing, key) != value for key, value in comparisons.items())


def _upsert_document_batch(
    session: Session,
    batch: list[dict[str, Any]],
    result: ImportResult,
) -> None:
    external_ids = [row["external_id"] for row in batch]
    existing_by_id = {
        document.external_id: document
        for document in session.scalars(
            select(Document).where(Document.external_id.in_(external_ids))
        )
    }
    for values in batch:
        existing = existing_by_id.get(values["external_id"])
        if existing is None:
            result.documents_inserted += 1
        elif _document_changed(existing, values):
            result.documents_updated += 1
        else:
            result.documents_unchanged += 1

    table = Document.__table__
    statement = pg_insert(table).values(batch)
    excluded = statement.excluded
    mutable_columns = (
        "title",
        "source_type",
        "document_type_code",
        "publisher",
        "source_url",
        "revision",
        "published_at",
        "access_level",
        "file_sha256",
        "metadata",
    )
    changed = or_(
        *(table.c[name].is_distinct_from(getattr(excluded, name)) for name in mutable_columns)
    )
    update_values = {name: getattr(excluded, name) for name in mutable_columns}
    update_values["updated_at"] = func.now()
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.external_id],
        set_=update_values,
        where=changed,
    )
    session.execute(statement)


def _chunk_changed(existing: DocumentChunk, values: dict[str, Any]) -> bool:
    comparisons = {
        "page_number": values["page_number"],
        "page_start": values["page_start"],
        "page_end": values["page_end"],
        "section_path": values["section_path"],
        "content": values["content"],
        "content_hash": values["content_hash"],
        "metadata_json": values["metadata"],
    }
    return any(getattr(existing, key) != value for key, value in comparisons.items())


def _upsert_chunk_batch(
    session: Session,
    batch: list[dict[str, Any]],
    result: ImportResult,
) -> None:
    document_ids = {row["document_id"] for row in batch}
    batch_keys = {(row["document_id"], row["chunk_index"]) for row in batch}
    existing_by_key = {
        (chunk.document_id, chunk.chunk_index): chunk
        for chunk in session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id.in_(document_ids))
        )
        if (chunk.document_id, chunk.chunk_index) in batch_keys
    }

    for values in batch:
        key = (values["document_id"], values["chunk_index"])
        existing = existing_by_key.get(key)
        if existing is None:
            result.chunks_inserted += 1
            continue
        if existing.content_hash == values["content_hash"]:
            previous_error = (existing.metadata_json or {}).get("embedding_error")
            if previous_error is not None:
                values["metadata"] = dict(values["metadata"])
                values["metadata"]["embedding_error"] = previous_error
        if _chunk_changed(existing, values):
            result.chunks_updated += 1
        else:
            result.chunks_unchanged += 1

    table = DocumentChunk.__table__
    statement = pg_insert(table).values(batch)
    excluded = statement.excluded
    content_changed = table.c.content_hash.is_distinct_from(excluded.content_hash)
    mutable_columns = (
        "page_number",
        "page_start",
        "page_end",
        "section_path",
        "content",
        "content_hash",
        "metadata",
    )
    changed = or_(
        *(table.c[name].is_distinct_from(getattr(excluded, name)) for name in mutable_columns)
    )
    update_values: dict[str, Any] = {
        name: getattr(excluded, name) for name in mutable_columns
    }
    update_values.update(
        {
            "embedding": case((content_changed, None), else_=table.c.embedding),
            "embedding_model": case(
                (content_changed, None), else_=table.c.embedding_model
            ),
            "embedding_dimension": case(
                (content_changed, None), else_=table.c.embedding_dimension
            ),
            "embedding_status": case(
                (content_changed, "pending"), else_=table.c.embedding_status
            ),
            "updated_at": func.now(),
        }
    )
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.document_id, table.c.chunk_index],
        index_where=table.c.document_version_id.is_(None),
        set_=update_values,
        where=changed,
    )
    session.execute(statement)


def import_accident_dataset(
    scan: ScanResult,
    session_factory: Callable[[], Session],
    batch_size: int = 500,
) -> ImportResult:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    if scan.errors:
        raise IngestionError("Dry-run validation failed: " + "; ".join(scan.errors))
    with session_factory() as safety_session:
        bind = safety_session.get_bind()
        if bind is None:
            raise IngestionError("Database session is not bound to an engine")
        assert_isolated_test_database(str(bind.url))

    result = ImportResult()
    document_rows = [
        document_values(
            document,
            scan.raw_metadata.get((document.source_type, document.source_row_id)),
        )
        for document in scan.selected_documents
    ]
    for batch_number, batch in enumerate(_chunks(document_rows, batch_size), start=1):
        try:
            with session_factory() as session, session.begin():
                _upsert_document_batch(session, batch, result)
            result.committed_document_batches += 1
        except Exception as exc:
            raise IngestionError(
                f"Document batch {batch_number} failed and was rolled back"
            ) from exc

    external_ids = [document.external_id for document in scan.selected_documents]
    with session_factory() as session:
        database_ids = dict(
            session.execute(
                select(Document.external_id, Document.id).where(
                    Document.external_id.in_(external_ids)
                )
            ).tuples().all()
        )
    source_to_database_id = {
        document.document_id: database_ids[document.external_id]
        for document in scan.selected_documents
    }
    selected_by_id = scan.selected_by_document_id
    chunk_batch: list[dict[str, Any]] = []
    batch_number = 0
    chunks_path = scan.data_dir / "accident_chunks.jsonl"
    for line_number, record in iter_jsonl(chunks_path):
        source_document_id = _required_int(
            record,
            "document_id",
            f"{chunks_path.name}:{line_number}",
        )
        document = selected_by_id.get(source_document_id)
        if document is None:
            continue
        chunk_batch.append(
            chunk_values(
                record,
                document,
                source_to_database_id[source_document_id],
            )
        )
        if len(chunk_batch) < batch_size:
            continue
        batch_number += 1
        try:
            with session_factory() as session, session.begin():
                _upsert_chunk_batch(session, chunk_batch, result)
            result.committed_chunk_batches += 1
        except Exception as exc:
            raise IngestionError(
                f"Chunk batch {batch_number} failed and was rolled back"
            ) from exc
        chunk_batch = []

    if chunk_batch:
        batch_number += 1
        try:
            with session_factory() as session, session.begin():
                _upsert_chunk_batch(session, chunk_batch, result)
            result.committed_chunk_batches += 1
        except Exception as exc:
            raise IngestionError(
                f"Chunk batch {batch_number} failed and was rolled back"
            ) from exc
    return result
