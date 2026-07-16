from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DocumentScopeArguments:
    source_types: tuple[str, ...] | None
    document_ids: tuple[UUID, ...] | None
    unrestricted: bool


def add_document_scope_arguments(
    parser: argparse.ArgumentParser,
    *,
    all_option: str,
    all_help: str,
) -> None:
    parser.add_argument(
        "--source-type",
        dest="source_types",
        action="append",
        help="처리할 documents.source_type. 여러 번 지정할 수 있습니다.",
    )
    parser.add_argument(
        "--document-id",
        dest="document_ids",
        action="append",
        type=UUID,
        help="처리할 문서 UUID. 여러 번 지정할 수 있습니다.",
    )
    parser.add_argument(all_option, action="store_true", help=all_help)


def parse_document_scope(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    all_attribute: str,
) -> DocumentScopeArguments:
    source_types = tuple(
        dict.fromkeys(source_type.strip() for source_type in args.source_types or ())
    ) or None
    if source_types is not None and any(not source_type for source_type in source_types):
        parser.error("--source-type에는 빈 값을 사용할 수 없습니다.")
    document_ids = tuple(dict.fromkeys(args.document_ids or ())) or None
    unrestricted = bool(getattr(args, all_attribute))

    if unrestricted and (source_types is not None or document_ids is not None):
        parser.error(
            f"--{all_attribute.replace('_', '-')}은 "
            "--source-type 또는 --document-id와 함께 사용할 수 없습니다."
        )
    if not unrestricted and source_types is None and document_ids is None:
        parser.error(
            "--source-type, --document-id 또는 명시적인 전체 처리 옵션 중 하나가 필요합니다."
        )
    return DocumentScopeArguments(
        source_types=source_types,
        document_ids=document_ids,
        unrestricted=unrestricted,
    )
