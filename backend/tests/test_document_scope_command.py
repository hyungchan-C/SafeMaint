import argparse
from uuid import uuid4

import pytest

from app.commands.document_scope import (
    add_document_scope_arguments,
    parse_document_scope,
)


def build_scope_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_document_scope_arguments(
        parser,
        all_option="--all-pending",
        all_help="all",
    )
    return parser


def test_scope_requires_an_explicit_selector() -> None:
    parser = build_scope_parser()

    with pytest.raises(SystemExit):
        parse_document_scope(
            parser,
            parser.parse_args([]),
            all_attribute="all_pending",
        )


def test_scope_accepts_source_types_and_document_ids_as_intersection() -> None:
    parser = build_scope_parser()
    document_id = uuid4()
    args = parser.parse_args(
        [
            "--source-type",
            "incident",
            "--source-type",
            "manual",
            "--document-id",
            str(document_id),
        ]
    )

    scope = parse_document_scope(parser, args, all_attribute="all_pending")

    assert scope.source_types == ("incident", "manual")
    assert scope.document_ids == (document_id,)
    assert scope.unrestricted is False


def test_all_scope_cannot_be_combined_with_filters() -> None:
    parser = build_scope_parser()
    args = parser.parse_args(["--all-pending", "--source-type", "incident"])

    with pytest.raises(SystemExit):
        parse_document_scope(parser, args, all_attribute="all_pending")


def test_scope_rejects_blank_source_type() -> None:
    parser = build_scope_parser()
    args = parser.parse_args(["--source-type", " "])

    with pytest.raises(SystemExit):
        parse_document_scope(parser, args, all_attribute="all_pending")
