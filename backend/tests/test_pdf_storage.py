from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from app.services import pdf_storage
from app.services.pdf_storage import (
    EmptyPdfUploadError,
    InvalidPdfHeaderError,
    PdfUploadIOError,
    PdfUploadTooLargeError,
    stage_pdf_upload,
)


def _parts(storage_dir: Path) -> list[Path]:
    return list(storage_dir.glob(".pdf-upload-*.part"))


@pytest.mark.parametrize("size", [15, 16])
def test_stream_accepts_files_at_or_below_limit(tmp_path: Path, size: int) -> None:
    content = b"%PDF-" + (b"x" * (size - 5))

    staged = stage_pdf_upload(
        BytesIO(content),
        tmp_path,
        max_bytes=16,
        chunk_bytes=3,
    )

    assert staged.file_size == size
    assert staged.sha256 == hashlib.sha256(content).hexdigest()
    final_path = staged.move_to(tmp_path / "stored.pdf")
    assert final_path.read_bytes() == content
    assert _parts(tmp_path) == []


def test_stream_rejects_one_byte_over_limit_and_cleans_temp(tmp_path: Path) -> None:
    with pytest.raises(PdfUploadTooLargeError):
        stage_pdf_upload(
            BytesIO(b"%PDF-" + (b"x" * 12)),
            tmp_path,
            max_bytes=16,
            chunk_bytes=4,
        )

    assert _parts(tmp_path) == []


def test_stream_accepts_unlimited_file_and_keeps_hash_and_size(
    tmp_path: Path,
) -> None:
    content = b"%PDF-" + (b"x" * 1024)

    staged = stage_pdf_upload(
        BytesIO(content),
        tmp_path,
        max_bytes=None,
        chunk_bytes=7,
    )

    assert staged.file_size == len(content)
    assert staged.sha256 == hashlib.sha256(content).hexdigest()
    assert staged.move_to(tmp_path / "unlimited.pdf").read_bytes() == content


def test_stream_rejects_empty_file_and_cleans_temp(tmp_path: Path) -> None:
    with pytest.raises(EmptyPdfUploadError):
        stage_pdf_upload(BytesIO(b""), tmp_path, max_bytes=16)

    assert _parts(tmp_path) == []


def test_stream_rejects_invalid_header_and_cleans_temp(tmp_path: Path) -> None:
    with pytest.raises(InvalidPdfHeaderError):
        stage_pdf_upload(BytesIO(b"not-a-pdf"), tmp_path, max_bytes=32, chunk_bytes=2)

    assert _parts(tmp_path) == []


def test_stream_cleans_temp_after_read_error(tmp_path: Path) -> None:
    class BrokenReader:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"%PDF-"
            raise OSError("simulated read failure")

    with pytest.raises(PdfUploadIOError, match="스트림"):
        stage_pdf_upload(BrokenReader(), tmp_path, max_bytes=32)  # type: ignore[arg-type]

    assert _parts(tmp_path) == []


def test_stream_cleans_temp_after_write_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_write(_destination, _chunk: bytes) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(pdf_storage, "_write_chunk", fail_write)

    with pytest.raises(PdfUploadIOError, match="쓰지 못했습니다"):
        stage_pdf_upload(BytesIO(b"%PDF-valid"), tmp_path, max_bytes=32)

    assert _parts(tmp_path) == []


def test_move_failure_preserves_owned_temp_until_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staged = stage_pdf_upload(BytesIO(b"%PDF-valid"), tmp_path, max_bytes=32)

    def fail_replace(_source, _destination) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(pdf_storage.os, "replace", fail_replace)
    with pytest.raises(PdfUploadIOError, match="최종 파일"):
        staged.move_to(tmp_path / "stored.pdf")
    assert len(_parts(tmp_path)) == 1

    staged.cleanup()
    assert _parts(tmp_path) == []


def test_zero_direct_limit_is_rejected_in_favor_of_none(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        stage_pdf_upload(BytesIO(b"%PDF-valid"), tmp_path, max_bytes=0)
