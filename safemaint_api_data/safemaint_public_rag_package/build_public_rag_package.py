#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SafeMaint 공용 안전자료 RAG 데이터 패키지 생성기

직접 읽는 입력은 정확히 3개입니다.
1) safemaint_accident_preprocessing/output/domestic_cases.csv
2) safemaint_accident_preprocessing/output/fatal_cases.csv
3) safemaint_smart_search/output/smart_search_documents.csv

읽지 않는 파일:
- smart_search_terms.csv
- smart_search_runs.csv
- smart_search_hits.csv
- occurrence_type 라벨링 결과 전체

생성:
- 로컬 검증용 rag_documents/rag_chunks/embedding 작업 파일
- SafeMaint 표준 documents/document_chunks 스키마용 JSONL
- Ed25519 서명된 공용 RAG 배포 ZIP

기존 수집·전처리 프로젝트와 결과 파일은 읽기만 하며 수정하지 않습니다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT_VERSION = "1.0.0"
DATA_SCOPE = "public"
PACKAGE_SCHEMA_VERSION = "0003_secure_documents"
PACKAGE_FILES = {
    "manifest.json",
    "documents.jsonl",
    "chunks.jsonl",
    "signature.ed25519",
}

DOMESTIC_COLUMNS = [
    "id", "title", "accident_date_text", "location", "location_sido",
    "location_sigungu", "location_detail", "content_text", "content_raw",
    "boardno", "business", "detailed_business", "causal_object",
]
FATAL_COLUMNS = [
    "id", "title", "accident_date_text", "location", "location_sido",
    "location_sigungu", "location_detail", "content_text", "content_raw",
    "title_raw",
]
SMART_COLUMNS = [
    "document_id", "source_doc_id", "category_code", "category_name",
    "source_type", "title", "content_raw", "content_clean", "keyword_raw",
    "keyword_clean", "filepath", "image_path_json", "med_thumb_yn",
    "media_style", "content_hash", "first_seen_at", "last_seen_at",
    "updated_at",
]

DOCUMENT_COLUMNS = [
    "document_id", "data_scope", "source_table", "source_type", "source_id",
    "title", "content", "content_hash", "metadata_json",
]
CHUNK_COLUMNS = [
    "embedding_row", "chunk_id", "document_id", "data_scope", "source_table",
    "source_type", "source_id", "chunk_index", "title", "chunk_text",
    "char_count", "content_hash", "metadata_json",
]


class PipelineError(RuntimeError):
    pass


def configure_csv_field_limit() -> None:
    value = sys.maxsize
    while True:
        try:
            csv.field_size_limit(value)
            return
        except OverflowError:
            value //= 10


configure_csv_field_limit()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def normalize_text(value: Any, preserve_newlines: bool = False) -> str:
    text = clean(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if preserve_newlines:
        lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    return re.sub(r"\s+", " ", text).strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def stable_short_hash(text: str, length: int = 32) -> str:
    return sha256_text(text)[:length]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def json_lines_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def load_private_key(value: str | Path) -> Ed25519PrivateKey:
    path = Path(value).expanduser()
    try:
        material = path.read_bytes()
    except OSError as exc:
        raise PipelineError(f"Ed25519 개인키를 읽을 수 없습니다: {path}") from exc
    if material.startswith(b"-----BEGIN"):
        try:
            key = serialization.load_pem_private_key(material, password=None)
        except (TypeError, ValueError) as exc:
            raise PipelineError("Ed25519 PEM 개인키 형식이 올바르지 않습니다.") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise PipelineError("패키지 개인키는 Ed25519 키여야 합니다.")
        return key
    if len(material) != 32:
        raise PipelineError("Raw Ed25519 개인키는 정확히 32바이트여야 합니다.")
    return Ed25519PrivateKey.from_private_bytes(material)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PipelineError(f"설정 파일이 없습니다: {path}")
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = {
        "package_name", "package_version", "chunk_size_chars",
        "chunk_overlap_chars", "embedding_model", "embedding_dimension",
        "embedding_max_length", "embedding_batch_size_gpu",
        "embedding_batch_size_cpu", "normalize_embeddings",
    }
    missing = sorted(required - set(config))
    if missing:
        raise PipelineError(f"config.json 필수 항목 누락: {missing}")
    chunk_size = int(config["chunk_size_chars"])
    overlap = int(config["chunk_overlap_chars"])
    if chunk_size < 300:
        raise PipelineError("chunk_size_chars는 300 이상이어야 합니다.")
    if overlap < 0 or overlap >= chunk_size:
        raise PipelineError("chunk_overlap_chars는 0 이상, chunk_size_chars 미만이어야 합니다.")
    if int(config["embedding_dimension"]) != 1024:
        raise PipelineError("BAAI/bge-m3 dense embedding 차원은 1024로 고정합니다.")
    return config


def read_csv(path: Path, expected_columns: Sequence[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise PipelineError(f"필수 입력 파일이 없습니다: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        actual = reader.fieldnames or []
        if actual != list(expected_columns):
            raise PipelineError(
                f"입력 컬럼이 예상과 다릅니다: {path}\n"
                f"예상: {list(expected_columns)}\n실제: {actual}"
            )
        rows: list[dict[str, str]] = []
        for line_no, row in enumerate(reader, start=2):
            if None in row:
                raise PipelineError(f"CSV 열 개수 오류: {path}:{line_no}")
            rows.append({column: row.get(column, "") for column in expected_columns})
    return rows


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
            count += 1
    os.replace(temp, path)
    return count


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    os.replace(temp, path)
    return count


def parse_json_or_default(value: str, default: Any) -> Any:
    text = clean(value)
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


@dataclass(frozen=True)
class Paths:
    project_dir: Path
    base_dir: Path
    config_path: Path
    domestic_path: Path
    fatal_path: Path
    smart_path: Path
    output_dir: Path
    work_dir: Path
    package_zip: Path


def resolve_paths(args: argparse.Namespace, config: dict[str, Any]) -> Paths:
    project_dir = Path(__file__).resolve().parent
    base_dir = Path(args.base_dir).resolve() if args.base_dir else project_dir.parent
    domestic_path = (
        Path(args.domestic).resolve() if args.domestic else
        base_dir / "safemaint_accident_preprocessing" / "output" / "domestic_cases.csv"
    )
    fatal_path = (
        Path(args.fatal).resolve() if args.fatal else
        base_dir / "safemaint_accident_preprocessing" / "output" / "fatal_cases.csv"
    )
    smart_path = (
        Path(args.smart_documents).resolve() if args.smart_documents else
        base_dir / "safemaint_smart_search" / "output" / "smart_search_documents.csv"
    )
    output_dir = Path(args.output_dir).resolve() if args.output_dir else project_dir / "output"
    package_folder_name = f"{config['package_name']}_v{config['package_version']}"
    return Paths(
        project_dir=project_dir,
        base_dir=base_dir,
        config_path=project_dir / "config.json",
        domestic_path=domestic_path,
        fatal_path=fatal_path,
        smart_path=smart_path,
        output_dir=output_dir,
        work_dir=output_dir / "work",
        package_zip=output_dir / f"{package_folder_name}.zip",
    )


def validate_unique(rows: Sequence[dict[str, str]], key: str, name: str) -> None:
    values = [clean(row[key]) for row in rows]
    empty = sum(1 for value in values if not value)
    duplicates = len(values) - len(set(values))
    if empty:
        raise PipelineError(f"{name}.{key} 빈 값: {empty}건")
    if duplicates:
        raise PipelineError(f"{name}.{key} 중복: {duplicates}건")


def validate_inputs(paths: Paths) -> dict[str, Any]:
    domestic = read_csv(paths.domestic_path, DOMESTIC_COLUMNS)
    fatal = read_csv(paths.fatal_path, FATAL_COLUMNS)
    smart = read_csv(paths.smart_path, SMART_COLUMNS)
    validate_unique(domestic, "id", "domestic_cases")
    validate_unique(fatal, "id", "fatal_cases")
    validate_unique(smart, "document_id", "smart_search_documents")

    def nonempty_title(rows: Sequence[dict[str, str]], name: str) -> None:
        count = sum(1 for row in rows if not normalize_text(row["title"]))
        if count:
            raise PipelineError(f"{name}.title 빈 값: {count}건")

    nonempty_title(domestic, "domestic_cases")
    nonempty_title(fatal, "fatal_cases")
    nonempty_title(smart, "smart_search_documents")

    report = {
        "passed": True,
        "checked_at": utc_now(),
        "inputs": {
            "domestic_cases": {
                "path": str(paths.domestic_path),
                "rows": len(domestic),
                "title_only_rows": sum(1 for row in domestic if not normalize_text(row["content_text"])),
                "sha256": sha256_file(paths.domestic_path),
            },
            "fatal_cases": {
                "path": str(paths.fatal_path),
                "rows": len(fatal),
                "empty_content_rows": sum(1 for row in fatal if not normalize_text(row["content_text"])),
                "sha256": sha256_file(paths.fatal_path),
            },
            "smart_search_documents": {
                "path": str(paths.smart_path),
                "rows": len(smart),
                "empty_content_rows": sum(1 for row in smart if not normalize_text(row["content_clean"])),
                "source_type_counts": dict(Counter(clean(row["source_type"]) or "unknown" for row in smart)),
                "sha256": sha256_file(paths.smart_path),
            },
        },
        "direct_input_files": 3,
        "unused_files": [
            "smart_search_terms.csv", "smart_search_runs.csv", "smart_search_hits.csv",
            "occurrence_type 라벨링 결과",
        ],
    }
    return report


def metadata_without_empty(items: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = clean(value)
            if not value:
                continue
        if isinstance(value, (list, dict)) and not value:
            continue
        result[key] = value
    return result


def make_document(
    *, source_table: str, source_type: str, source_id: str,
    title: str, body: str, metadata: dict[str, Any],
) -> dict[str, Any]:
    title = normalize_text(title)
    body = normalize_text(body, preserve_newlines=True)
    if not title:
        raise PipelineError(f"제목이 비었습니다: {source_table}/{source_id}")
    content = body if body else title
    identity = f"{source_table}:{source_id}"
    document_id = f"public:{source_table}:{stable_short_hash(identity, 24)}"
    return {
        "document_id": document_id,
        "data_scope": DATA_SCOPE,
        "source_table": source_table,
        "source_type": source_type,
        "source_id": source_id,
        "title": title,
        "content": content,
        "content_hash": sha256_text(normalize_text(title) + "\n" + normalize_text(content)),
        "metadata_json": canonical_json(metadata_without_empty(metadata)),
    }


def build_documents(paths: Paths) -> list[dict[str, Any]]:
    domestic = read_csv(paths.domestic_path, DOMESTIC_COLUMNS)
    fatal = read_csv(paths.fatal_path, FATAL_COLUMNS)
    smart = read_csv(paths.smart_path, SMART_COLUMNS)

    documents: list[dict[str, Any]] = []
    for row in sorted(domestic, key=lambda item: int(clean(item["id"]))):
        source_id = clean(row["id"])
        body = normalize_text(row["content_text"], preserve_newlines=True)
        documents.append(make_document(
            source_table="domestic_cases",
            source_type="domestic_case",
            source_id=source_id,
            title=row["title"],
            body=body,
            metadata={
                "boardno": row["boardno"],
                "business": row["business"],
                "detailed_business": row["detailed_business"],
                "causal_object": row["causal_object"],
                "accident_date_text": row["accident_date_text"],
                "location": row["location"],
                "location_sido": row["location_sido"],
                "location_sigungu": row["location_sigungu"],
                "location_detail": row["location_detail"],
                "content_quality": "full_text" if body else "title_only",
            },
        ))

    for row in sorted(fatal, key=lambda item: int(clean(item["id"]))):
        documents.append(make_document(
            source_table="fatal_cases",
            source_type="fatal_case",
            source_id=clean(row["id"]),
            title=row["title"],
            body=row["content_text"],
            metadata={
                "title_raw": row["title_raw"],
                "accident_date_text": row["accident_date_text"],
                "location": row["location"],
                "location_sido": row["location_sido"],
                "location_sigungu": row["location_sigungu"],
                "location_detail": row["location_detail"],
                "content_quality": "full_text",
            },
        ))

    for row in sorted(smart, key=lambda item: clean(item["document_id"])):
        source_type = clean(row["source_type"]) or "safety_document"
        body = normalize_text(row["content_clean"], preserve_newlines=True)
        documents.append(make_document(
            source_table="smart_search_documents",
            source_type=source_type,
            source_id=clean(row["document_id"]),
            title=row["title"],
            body=body,
            metadata={
                "source_doc_id": row["source_doc_id"],
                "category_code": row["category_code"],
                "category_name": row["category_name"],
                "keyword_clean": row["keyword_clean"],
                "filepath": row["filepath"],
                "image_paths": parse_json_or_default(row["image_path_json"], []),
                "med_thumb_yn": row["med_thumb_yn"],
                "media_style": row["media_style"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "source_updated_at": row["updated_at"],
                "content_quality": "full_text" if body else "title_only",
            },
        ))

    ids = [row["document_id"] for row in documents]
    if len(ids) != len(set(ids)):
        raise PipelineError("생성된 document_id가 중복되었습니다.")
    return documents


def load_metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata_json", "{}")
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def source_label(document: dict[str, Any]) -> str:
    metadata = load_metadata(document)
    if document["source_type"] == "domestic_case":
        return "국내재해사례"
    if document["source_type"] == "fatal_case":
        return "사고사망사례"
    return clean(metadata.get("category_name")) or clean(document["source_type"]) or "안전자료"


def build_header(document: dict[str, Any]) -> str:
    metadata = load_metadata(document)
    lines = [f"[자료유형] {source_label(document)}", f"[제목] {document['title']}"]
    if document["source_type"] == "domestic_case":
        business = clean(metadata.get("business"))
        if business:
            lines.append(f"[업종] {business}")
    elif document["source_type"] == "fatal_case":
        location = clean(metadata.get("location"))
        if location:
            lines.append(f"[장소] {location}")
    return "\n".join(lines)


def find_break(text: str, start: int, desired_end: int, minimum_end: int) -> int:
    candidates = ["\n\n", "\n", "다. ", "요. ", ". ", "。", "; ", ", ", " "]
    segment = text[start:desired_end]
    for marker in candidates:
        position = segment.rfind(marker)
        if position >= 0:
            end = start + position + len(marker)
            if end >= minimum_end:
                return end
    return desired_end


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    text = normalize_text(text, preserve_newlines=True)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        desired_end = min(start + max_chars, length)
        if desired_end < length:
            minimum_end = min(desired_end, start + max(1, int(max_chars * 0.60)))
            end = find_break(text, start, desired_end, minimum_end)
        else:
            end = length
        if end <= start:
            end = min(start + max_chars, length)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= length:
            break
        next_start = max(start + 1, end - overlap)
        while next_start < end and text[next_start].isspace():
            next_start += 1
        start = next_start
    return pieces


def build_chunks(documents: Sequence[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    chunk_size = int(config["chunk_size_chars"])
    overlap = int(config["chunk_overlap_chars"])
    chunks: list[dict[str, Any]] = []
    embedding_row = 0
    for document in documents:
        header = build_header(document)
        content_quality = load_metadata(document).get("content_quality", "full_text")
        body = document["content"] if content_quality != "title_only" else ""
        content_prefix = "\n[내용] "
        body_budget = chunk_size - len(header) - len(content_prefix)
        if body_budget < 160:
            raise PipelineError(
                f"헤더가 너무 길어 청크를 만들 수 없습니다: {document['document_id']}"
            )
        body_overlap = min(overlap, max(0, body_budget - 1))
        body_pieces = split_text(body, body_budget, body_overlap) if body else [""]
        for index, body_piece in enumerate(body_pieces):
            chunk_text = header if not body_piece else header + content_prefix + body_piece
            chunk_text = chunk_text.strip()
            if not chunk_text:
                raise PipelineError(f"빈 청크 생성: {document['document_id']}#{index}")
            if len(chunk_text) > chunk_size:
                raise PipelineError(
                    f"청크 길이 초과: {document['document_id']}#{index} "
                    f"({len(chunk_text)} > {chunk_size})"
                )
            chunk_id = (
                f"public:chunk:{stable_short_hash(document['document_id'] + ':' + str(index) + ':' + sha256_text(chunk_text), 32)}"
            )
            chunks.append({
                "embedding_row": embedding_row,
                "chunk_id": chunk_id,
                "document_id": document["document_id"],
                "data_scope": DATA_SCOPE,
                "source_table": document["source_table"],
                "source_type": document["source_type"],
                "source_id": document["source_id"],
                "chunk_index": index,
                "title": document["title"],
                "chunk_text": chunk_text,
                "char_count": len(chunk_text),
                "content_hash": sha256_text(chunk_text),
                "metadata_json": document["metadata_json"],
            })
            embedding_row += 1
    ids = [row["chunk_id"] for row in chunks]
    if len(ids) != len(set(ids)):
        raise PipelineError("생성된 chunk_id가 중복되었습니다.")
    return chunks


def public_source_mapping(source_type: str) -> tuple[str, str]:
    """Map collector-specific source names to the canonical SafeMaint schema."""
    mappings = {
        "domestic_case": ("incident", "public_incident"),
        "fatal_case": ("incident", "public_incident"),
        "law": ("regulation", "public_law"),
        "notice": ("regulation", "public_law"),
        "kosha_guide": ("guide", "public_guide"),
        "media": ("media", "public_media"),
    }
    try:
        return mappings[source_type]
    except KeyError as exc:
        raise PipelineError(
            f"SafeMaint 공용 문서 유형으로 매핑되지 않은 source_type입니다: {source_type!r}"
        ) from exc


def package_document_rows(
    documents: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for document in documents:
        source_type, document_type_code = public_source_mapping(
            clean(document["source_type"])
        )
        metadata = load_metadata(document)
        metadata.update(
            {
                "source_table": document["source_table"],
                "source_id": document["source_id"],
                "source_content_hash": document["content_hash"],
            }
        )
        rows.append(
            {
                "source_document_id": document["document_id"],
                "external_id": document["document_id"],
                "title": document["title"],
                "source_type": source_type,
                "document_type_code": document_type_code,
                "publisher": "한국산업안전보건공단",
                "source_url": None,
                "revision": None,
                "published_at": None,
                "access_level": "public",
                "metadata": metadata,
            }
        )
    return rows


def package_chunk_rows(
    chunks: Sequence[dict[str, Any]],
    embeddings: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_shape = (len(chunks), int(config["embedding_dimension"]))
    if tuple(embeddings.shape) != expected_shape:
        raise PipelineError(
            f"임베딩 shape 불일치: expected={expected_shape}, actual={tuple(embeddings.shape)}"
        )
    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        embedding_row = int(chunk["embedding_row"])
        metadata = load_metadata(chunk)
        metadata.update(
            {
                "source_table": chunk["source_table"],
                "source_id": chunk["source_id"],
                "source_chunk_id": chunk["chunk_id"],
            }
        )
        rows.append(
            {
                "source_document_id": chunk["document_id"],
                "source_chunk_id": chunk["chunk_id"],
                "chunk_index": int(chunk["chunk_index"]),
                "page_number": None,
                "page_start": None,
                "page_end": None,
                "section_path": [],
                "content": chunk["chunk_text"],
                "content_hash": chunk["content_hash"],
                "metadata": metadata,
                "embedding": [float(value) for value in embeddings[embedding_row]],
                "embedding_model": str(config["embedding_model"]),
                "embedding_dimension": int(config["embedding_dimension"]),
            }
        )
    return rows


def pipeline_fingerprint(
    input_report: dict[str, Any], config: dict[str, Any], chunks: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "script_version": SCRIPT_VERSION,
        "inputs": {
            key: value["sha256"] for key, value in input_report["inputs"].items()
        },
        "config": config,
        "chunk_count": len(chunks),
        "first_chunk": chunks[0]["content_hash"] if chunks else None,
        "last_chunk": chunks[-1]["content_hash"] if chunks else None,
    }
    return sha256_text(canonical_json(payload))


def detect_device() -> tuple[str, bool]:
    try:
        import torch
    except ImportError as exc:
        raise PipelineError(
            "torch가 설치되지 않았습니다. run_build_package.bat 또는 "
            "pip install -r requirements.txt를 먼저 실행하세요."
        ) from exc
    if torch.cuda.is_available():
        return "cuda", True
    return "cpu", False


def embed_chunks(
    chunks: Sequence[dict[str, Any]], config: dict[str, Any], work_dir: Path,
    fingerprint: str, reset: bool, batch_size_override: int | None,
) -> tuple[Path, dict[str, Any]]:
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise PipelineError(
            "임베딩 패키지가 설치되지 않았습니다. run_build_package.bat을 실행하세요."
        ) from exc

    work_dir.mkdir(parents=True, exist_ok=True)
    embeddings_path = work_dir / "rag_embeddings.npy"
    progress_path = work_dir / "embedding_progress.json"
    dimension = int(config["embedding_dimension"])
    total = len(chunks)
    device, cuda_available = detect_device()
    batch_size = int(batch_size_override or (
        config["embedding_batch_size_gpu"] if cuda_available
        else config["embedding_batch_size_cpu"]
    ))
    if batch_size <= 0:
        raise PipelineError("batch_size는 1 이상이어야 합니다.")

    if reset:
        embeddings_path.unlink(missing_ok=True)
        progress_path.unlink(missing_ok=True)

    completed = 0
    if progress_path.exists() or embeddings_path.exists():
        if not (progress_path.exists() and embeddings_path.exists()):
            raise PipelineError(
                "임베딩 진행 파일이 불완전합니다. --reset-embeddings를 붙여 다시 실행하세요."
            )
        with progress_path.open("r", encoding="utf-8") as handle:
            progress = json.load(handle)
        expected = {
            "fingerprint": fingerprint,
            "total_rows": total,
            "dimension": dimension,
            "embedding_model": config["embedding_model"],
        }
        for key, value in expected.items():
            if progress.get(key) != value:
                raise PipelineError(
                    "입력·설정·청크가 이전 임베딩 작업과 다릅니다. "
                    "--reset-embeddings를 붙여 새로 실행하세요."
                )
        completed = int(progress.get("completed_rows", 0))
        if completed < 0 or completed > total:
            raise PipelineError("embedding_progress.json의 completed_rows가 잘못되었습니다.")
        matrix = np.lib.format.open_memmap(
            embeddings_path, mode="r+", dtype=np.float32, shape=(total, dimension)
        )
    else:
        matrix = np.lib.format.open_memmap(
            embeddings_path, mode="w+", dtype=np.float32, shape=(total, dimension)
        )
        atomic_write_json(progress_path, {
            "fingerprint": fingerprint,
            "total_rows": total,
            "dimension": dimension,
            "embedding_model": config["embedding_model"],
            "completed_rows": 0,
            "device": device,
            "batch_size": batch_size,
            "started_at": utc_now(),
            "updated_at": utc_now(),
        })

    if completed == total:
        print(f"[임베딩] 이미 완료됨: {total:,}/{total:,}")
    else:
        print(f"[임베딩] 모델 로드: {config['embedding_model']} / device={device}")
        model = SentenceTransformer(str(config["embedding_model"]), device=device)
        model.max_seq_length = int(config["embedding_max_length"])
        for start in range(completed, total, batch_size):
            end = min(start + batch_size, total)
            texts = [str(chunks[index]["chunk_text"]) for index in range(start, end)]
            vectors = model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=bool(config["normalize_embeddings"]),
            )
            vectors = np.asarray(vectors, dtype=np.float32)
            if vectors.shape != (end - start, dimension):
                raise PipelineError(
                    f"임베딩 shape 오류: {vectors.shape}, 예상 {(end - start, dimension)}"
                )
            if not np.isfinite(vectors).all():
                raise PipelineError(f"NaN/Inf 임베딩 발견: rows {start}~{end - 1}")
            matrix[start:end] = vectors
            matrix.flush()
            completed = end
            atomic_write_json(progress_path, {
                "fingerprint": fingerprint,
                "total_rows": total,
                "dimension": dimension,
                "embedding_model": config["embedding_model"],
                "completed_rows": completed,
                "device": device,
                "batch_size": batch_size,
                "updated_at": utc_now(),
            })
            percent = 100.0 * completed / max(total, 1)
            print(f"[임베딩] {completed:,}/{total:,} ({percent:6.2f}%)")

    sample_size = min(total, 1000)
    if sample_size:
        sample = np.asarray(matrix[:sample_size], dtype=np.float32)
        norms = np.linalg.norm(sample, axis=1)
        if bool(config["normalize_embeddings"]):
            max_error = float(np.max(np.abs(norms - 1.0)))
            if max_error > 0.02:
                raise PipelineError(f"정규화 임베딩 norm 오차가 큽니다: {max_error}")
        else:
            max_error = None
    else:
        max_error = None
    del matrix
    return embeddings_path, {
        "device": device,
        "batch_size": batch_size,
        "rows": total,
        "dimension": dimension,
        "normalized": bool(config["normalize_embeddings"]),
        "sample_norm_max_error": max_error,
    }


def validate_outputs(
    documents: Sequence[dict[str, Any]], chunks: Sequence[dict[str, Any]],
    config: dict[str, Any], embeddings_path: Path | None,
) -> dict[str, Any]:
    document_ids = [row["document_id"] for row in documents]
    chunk_ids = [row["chunk_id"] for row in chunks]
    document_set = set(document_ids)
    orphan_chunks = sum(1 for row in chunks if row["document_id"] not in document_set)
    chunk_size = int(config["chunk_size_chars"])
    overlong = sum(1 for row in chunks if int(row["char_count"]) > chunk_size)
    empty_chunks = sum(1 for row in chunks if not clean(row["chunk_text"]))
    wrong_embedding_rows = sum(
        1 for index, row in enumerate(chunks) if int(row["embedding_row"]) != index
    )
    source_document_counts = dict(Counter(row["source_type"] for row in documents))
    source_chunk_counts = dict(Counter(row["source_type"] for row in chunks))

    embedding_report: dict[str, Any] = {"present": embeddings_path is not None}
    if embeddings_path is not None:
        import numpy as np
        array = np.load(embeddings_path, mmap_mode="r")
        embedding_report.update({
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "finite": bool(np.isfinite(array[: min(len(array), 1000)]).all()),
        })
        expected_shape = (len(chunks), int(config["embedding_dimension"]))
        if tuple(array.shape) != expected_shape:
            raise PipelineError(f"임베딩 shape 불일치: {array.shape}, 예상 {expected_shape}")

    passed = all([
        len(document_ids) == len(set(document_ids)),
        len(chunk_ids) == len(set(chunk_ids)),
        orphan_chunks == 0,
        overlong == 0,
        empty_chunks == 0,
        wrong_embedding_rows == 0,
        (embeddings_path is None or embedding_report.get("finite") is True),
    ])
    report = {
        "passed": passed,
        "validated_at": utc_now(),
        "documents": len(documents),
        "chunks": len(chunks),
        "source_document_counts": source_document_counts,
        "source_chunk_counts": source_chunk_counts,
        "duplicate_document_ids": len(document_ids) - len(set(document_ids)),
        "duplicate_chunk_ids": len(chunk_ids) - len(set(chunk_ids)),
        "orphan_chunks": orphan_chunks,
        "empty_chunks": empty_chunks,
        "overlong_chunks": overlong,
        "wrong_embedding_rows": wrong_embedding_rows,
        "max_chunk_chars": max((int(row["char_count"]) for row in chunks), default=0),
        "embedding": embedding_report,
    }
    if not passed:
        raise PipelineError("출력 검증에 실패했습니다: " + canonical_json(report))
    return report


def write_signed_archive(
    output_path: Path,
    manifest: dict[str, Any],
    document_rows: Sequence[dict[str, Any]],
    chunk_rows: Sequence[dict[str, Any]],
    private_key_path: str | Path,
) -> dict[str, Any]:
    document_bytes = json_lines_bytes(document_rows)
    chunk_bytes = json_lines_bytes(chunk_rows)
    signed_manifest = {
        **manifest,
        "document_count": len(document_rows),
        "chunk_count": len(chunk_rows),
        "files": {
            "documents.jsonl": hashlib.sha256(document_bytes).hexdigest(),
            "chunks.jsonl": hashlib.sha256(chunk_bytes).hexdigest(),
        },
        "signature_algorithm": "Ed25519",
    }
    manifest_bytes = canonical_json_bytes(signed_manifest)
    signature = load_private_key(private_key_path).sign(manifest_bytes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_zip = output_path.with_suffix(output_path.suffix + ".tmp")
    with zipfile.ZipFile(
        temporary_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("documents.jsonl", document_bytes)
        archive.writestr("chunks.jsonl", chunk_bytes)
        archive.writestr("signature.ed25519", signature)
    os.replace(temporary_zip, output_path)
    with zipfile.ZipFile(output_path, "r") as archive:
        if set(archive.namelist()) != PACKAGE_FILES:
            raise PipelineError("생성된 패키지 파일 구성이 SafeMaint 표준과 다릅니다.")
    return signed_manifest


def assemble_package(
    paths: Paths, config: dict[str, Any], input_report: dict[str, Any],
    documents: Sequence[dict[str, Any]], chunks: Sequence[dict[str, Any]],
    embeddings_path: Path, validation_report: dict[str, Any], embedding_runtime: dict[str, Any],
    fingerprint: str, private_key_path: str | Path, previous_version: str | None,
) -> None:
    import numpy as np

    embeddings = np.load(embeddings_path, mmap_mode="r")
    document_rows = package_document_rows(documents)
    chunk_rows = package_chunk_rows(chunks, embeddings, config)
    manifest = {
        "package_version": config["package_version"],
        "created_at": utc_now(),
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "embedding_model": config["embedding_model"],
        "embedding_dimension": int(config["embedding_dimension"]),
        "sources": sorted({row["document_type_code"] for row in document_rows}),
        "previous_version": previous_version,
        "package_name": config["package_name"],
        "builder_script_version": SCRIPT_VERSION,
        "pipeline_fingerprint": fingerprint,
        "direct_inputs": {
            key: {
                "filename": Path(value["path"]).name,
                "rows": value["rows"],
                "sha256": value["sha256"],
            }
            for key, value in input_report["inputs"].items()
        },
        "unused_inputs": input_report["unused_files"],
        "outputs": {
            "documents": len(document_rows),
            "document_chunks": len(chunk_rows),
        },
        "chunking": {
            "method": "deterministic_character_chunking_with_safe_breaks",
            "chunk_size_chars": config["chunk_size_chars"],
            "chunk_overlap_chars": config["chunk_overlap_chars"],
            "header_repeated_per_chunk": True,
        },
        "embedding_runtime": {
            "max_length": config["embedding_max_length"],
            "normalize_embeddings": config["normalize_embeddings"],
            "dtype": "float32",
            "distance_metric": "cosine",
            **embedding_runtime,
        },
    }
    write_signed_archive(
        paths.package_zip,
        manifest,
        document_rows,
        chunk_rows,
        private_key_path,
    )

    atomic_write_json(paths.output_dir / "model_info.json", {
        "model": config["embedding_model"],
        "dimension": config["embedding_dimension"],
        "max_length": config["embedding_max_length"],
        "normalized": config["normalize_embeddings"],
        "dtype": "float32",
        "query_instruction_required": False,
    })
    atomic_write_json(paths.output_dir / "validation_report.json", validation_report)


def build_command(args: argparse.Namespace, paths: Paths, config: dict[str, Any]) -> None:
    private_key_path = args.private_key or os.getenv("PUBLIC_PACKAGE_PRIVATE_KEY", "").strip()
    if not args.skip_embeddings and not private_key_path:
        raise PipelineError(
            "서명된 패키지를 만들려면 --private-key 또는 "
            "PUBLIC_PACKAGE_PRIVATE_KEY가 필요합니다."
        )
    input_report = validate_inputs(paths)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths.output_dir / "input_validation_report.json", input_report)
    print("[입력 검증] 통과")
    for name, info in input_report["inputs"].items():
        print(f"  - {name}: {info['rows']:,}건")

    documents = build_documents(paths)
    chunks = build_chunks(documents, config)
    print(f"[통합 문서] {len(documents):,}건")
    print(f"[청크] {len(chunks):,}건 / 최대 {config['chunk_size_chars']}자 / overlap {config['chunk_overlap_chars']}자")

    paths.work_dir.mkdir(parents=True, exist_ok=True)
    write_csv(paths.work_dir / "rag_documents.csv", DOCUMENT_COLUMNS, documents)
    write_jsonl(paths.work_dir / "rag_documents.jsonl", documents)
    write_csv(paths.work_dir / "rag_chunks.csv", CHUNK_COLUMNS, chunks)
    write_jsonl(paths.work_dir / "rag_chunks.jsonl", chunks)

    fingerprint = pipeline_fingerprint(input_report, config, chunks)
    atomic_write_json(paths.work_dir / "build_state.json", {
        "pipeline_fingerprint": fingerprint,
        "documents": len(documents),
        "chunks": len(chunks),
        "created_at": utc_now(),
    })

    if args.skip_embeddings:
        validation = validate_outputs(documents, chunks, config, embeddings_path=None)
        atomic_write_json(paths.output_dir / "structure_validation_report.json", validation)
        print("[완료] --skip-embeddings 모드: 문서/청크 구조까지만 생성했습니다.")
        print(f"결과: {paths.work_dir}")
        return

    embeddings_path, embedding_runtime = embed_chunks(
        chunks=chunks,
        config=config,
        work_dir=paths.work_dir,
        fingerprint=fingerprint,
        reset=bool(args.reset_embeddings),
        batch_size_override=args.batch_size,
    )
    validation = validate_outputs(documents, chunks, config, embeddings_path=embeddings_path)
    assemble_package(
        paths=paths,
        config=config,
        input_report=input_report,
        documents=documents,
        chunks=chunks,
        embeddings_path=embeddings_path,
        validation_report=validation,
        embedding_runtime=embedding_runtime,
        fingerprint=fingerprint,
        private_key_path=private_key_path,
        previous_version=args.previous_version,
    )
    print("[완료] 공용 안전자료 RAG 데이터 패키지 생성 성공")
    print(f"ZIP:  {paths.package_zip}")


def validate_command(paths: Paths) -> None:
    report = validate_inputs(paths)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths.output_dir / "input_validation_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SafeMaint 공용 안전자료 RAG 패키지 생성")
    parser.add_argument("command", choices=["validate", "build"])
    parser.add_argument("--base-dir", help="기본 데이터 폴더. 기본값은 프로젝트 폴더의 상위 폴더")
    parser.add_argument("--domestic", help="domestic_cases.csv 직접 경로")
    parser.add_argument("--fatal", help="fatal_cases.csv 직접 경로")
    parser.add_argument("--smart-documents", help="smart_search_documents.csv 직접 경로")
    parser.add_argument("--output-dir", help="출력 폴더 직접 경로")
    parser.add_argument("--skip-embeddings", action="store_true", help="문서/청크까지만 생성")
    parser.add_argument("--reset-embeddings", action="store_true", help="기존 임베딩 진행상태를 삭제하고 처음부터 실행")
    parser.add_argument("--batch-size", type=int, help="임베딩 배치 크기 직접 지정")
    parser.add_argument(
        "--private-key",
        help="SafeMaint 공용 패키지 Ed25519 개인키 파일 경로",
    )
    parser.add_argument(
        "--previous-version",
        help="교체 대상이 되는 이전 공용 패키지 버전",
    )
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    project_dir = Path(__file__).resolve().parent
    config = read_config(project_dir / "config.json")
    paths = resolve_paths(args, config)
    try:
        if args.command == "validate":
            validate_command(paths)
        else:
            build_command(args, paths, config)
        return 0
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 실행을 중단했습니다. 임베딩은 다음 실행에서 이어집니다.", file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[예상하지 못한 오류] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
