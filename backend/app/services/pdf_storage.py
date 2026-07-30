from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


PDF_MAGIC = b"%PDF-"
PDF_UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024


class PdfUploadError(Exception):
    """Base class for validation and storage failures during PDF upload."""


class EmptyPdfUploadError(PdfUploadError):
    pass


class InvalidPdfHeaderError(PdfUploadError):
    pass


class PdfUploadTooLargeError(PdfUploadError):
    pass


class PdfUploadIOError(PdfUploadError):
    pass


@dataclass
class StagedPdfUpload:
    """A completely streamed PDF that has not yet been moved to its final name."""

    temp_path: Path | None
    file_size: int
    sha256: str

    def move_to(self, final_path: Path) -> Path:
        if self.temp_path is None:
            raise PdfUploadIOError("PDF 임시 파일이 이미 이동되었거나 정리되었습니다.")
        if final_path.parent.resolve() != self.temp_path.parent.resolve():
            raise PdfUploadIOError("PDF 최종 파일은 임시 파일과 같은 저장소에 있어야 합니다.")
        try:
            os.replace(self.temp_path, final_path)
        except OSError as error:
            raise PdfUploadIOError("PDF 최종 파일을 생성하지 못했습니다.") from error
        self.temp_path = None
        return final_path

    def cleanup(self) -> None:
        if self.temp_path is None:
            return
        try:
            self.temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            self.temp_path = None


def _write_chunk(destination: BinaryIO, chunk: bytes) -> None:
    destination.write(chunk)


def stage_pdf_upload(
    source: BinaryIO,
    storage_dir: Path,
    *,
    max_bytes: int | None,
    chunk_bytes: int = PDF_UPLOAD_CHUNK_BYTES,
) -> StagedPdfUpload:
    """Stream an uploaded PDF into a temporary file in ``storage_dir``.

    The actual bytes read determine the size limit. Content-Length is deliberately
    not used. The returned object owns the temporary file until ``move_to`` or
    ``cleanup`` is called.
    """

    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")

    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=storage_dir,
            prefix=".pdf-upload-",
            suffix=".part",
        )
    except OSError as error:
        raise PdfUploadIOError("PDF 임시 파일을 생성하지 못했습니다.") from error

    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    file_size = 0
    header = bytearray()

    try:
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                try:
                    chunk = source.read(chunk_bytes)
                except Exception as error:
                    raise PdfUploadIOError("PDF 업로드 스트림을 읽지 못했습니다.") from error

                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise PdfUploadIOError("PDF 업로드 스트림이 바이트를 반환하지 않았습니다.")

                if len(header) < len(PDF_MAGIC):
                    remaining = len(PDF_MAGIC) - len(header)
                    header.extend(chunk[:remaining])
                    if len(header) == len(PDF_MAGIC) and bytes(header) != PDF_MAGIC:
                        raise InvalidPdfHeaderError("PDF 파일 헤더가 올바르지 않습니다.")

                next_size = file_size + len(chunk)
                if max_bytes is not None and next_size > max_bytes:
                    raise PdfUploadTooLargeError(
                        f"파일 크기는 {max_bytes} 바이트를 넘을 수 없습니다."
                    )

                try:
                    _write_chunk(destination, chunk)
                except OSError as error:
                    raise PdfUploadIOError("PDF 임시 파일에 쓰지 못했습니다.") from error
                digest.update(chunk)
                file_size = next_size

            if file_size == 0:
                raise EmptyPdfUploadError("빈 파일은 업로드할 수 없습니다.")
            if bytes(header) != PDF_MAGIC:
                raise InvalidPdfHeaderError("PDF 파일 헤더가 올바르지 않습니다.")

            try:
                destination.flush()
                os.fsync(destination.fileno())
            except OSError as error:
                raise PdfUploadIOError("PDF 임시 파일을 디스크에 기록하지 못했습니다.") from error
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return StagedPdfUpload(
        temp_path=temp_path,
        file_size=file_size,
        sha256=digest.hexdigest(),
    )
