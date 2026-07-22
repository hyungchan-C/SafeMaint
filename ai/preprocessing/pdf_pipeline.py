"""
재사용 가능한 PDF -> 청크 파이프라인

지금(오토닉스 4개 파일 배치 처리)과 나중(F-01 서비스 내 업로드 기능)에
동일하게 쓰기 위해 함수 단위로 분리했습니다.

텍스트/구조 추출은 docling의 레이아웃 모델을 사용합니다. 폰트 크기 같은
문서별 매직넘버 튜닝 없이도 제조사가 다른 PDF에 일반적으로 적용하기 위함입니다.
GPU가 있으면 자동으로 사용하고, 없으면 CPU를 사용합니다(DOCLING_ACCELERATOR_DEVICE="auto").

출력은 docs/preprocessing-contract.md 규격을 따르는
{"document": {...}, "chunks": [...], "processing_metadata": {...}}입니다.
document는 문서 레코드 1건, chunks는 청크 레코드 N건이며 processing_metadata는
실제로 사용한 추출기와 안전한 폴백 정보를 담습니다.

사용법:
    from pdf_pipeline import process_pdf
    result = process_pdf(
        pdf_path="파일경로.pdf",
        product_type="포토센서",
        model_name="BTS Series",
        manufacturer="오토닉스",  # 기본값이라 생략 가능
        exclude_sections=["외형치수도", "모터 특성도", "제품 특성 데이터"],
    )
    result["document"]  # 문서 레코드 1건
    result["chunks"]    # 청크 레코드 N건
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

_HEADER_LABELS = {"section_header", "title"}
_SKIP_LABELS = {"picture", "page_header", "page_footer"}
DOCLING_ACCELERATOR_DEVICE = "auto"
_DEFAULT_MAX_FILE_BYTES = 200 * 1024 * 1024
_DEFAULT_MAX_PAGES = 500
_DEFAULT_NUM_THREADS = 4
_MAX_FALLBACK_REASON_LENGTH = 500
_MODEL_ARTIFACT_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".msgpack",
    ".onnx",
    ".pth",
    ".pt",
    ".safetensors",
}

logger = logging.getLogger(__name__)


class DoclingDeploymentError(RuntimeError):
    """Docling package or offline artifacts are not deployable."""


class DoclingConversionError(RuntimeError):
    """Docling is installed but a document could not be converted safely."""


class PdfProcessingLimitError(ValueError):
    """The PDF exceeds the configured worker processing limits."""


@dataclass(frozen=True, slots=True)
class DoclingRuntimeSettings:
    required: bool = True
    allow_pymupdf_fallback: bool = True
    artifacts_path: str | None = None
    offline: bool = False
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES
    max_pages: int = _DEFAULT_MAX_PAGES
    num_threads: int = _DEFAULT_NUM_THREADS
    accelerator_device: str = DOCLING_ACCELERATOR_DEVICE

    @classmethod
    def from_env(cls) -> "DoclingRuntimeSettings":
        return cls(
            required=_bool_env("DOCLING_REQUIRED", True),
            allow_pymupdf_fallback=_bool_env(
                "DOCLING_ALLOW_PYMUPDF_FALLBACK", True
            ),
            artifacts_path=os.getenv("DOCLING_ARTIFACTS_PATH", "").strip() or None,
            offline=_bool_env("DOCLING_OFFLINE", False),
            max_file_bytes=_positive_int_env(
                "DOCLING_MAX_FILE_BYTES", _DEFAULT_MAX_FILE_BYTES
            ),
            max_pages=_positive_int_env("DOCLING_MAX_PAGES", _DEFAULT_MAX_PAGES),
            num_threads=_positive_int_env(
                "DOCLING_NUM_THREADS", _DEFAULT_NUM_THREADS
            ),
            accelerator_device=os.getenv(
                "DOCLING_ACCELERATOR_DEVICE", DOCLING_ACCELERATOR_DEVICE
            ).strip()
            or DOCLING_ACCELERATOR_DEVICE,
        )


@dataclass(frozen=True, slots=True)
class SectionExtractionResult:
    sections: list[dict[str, Any]]
    processing_metadata: dict[str, Any]


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value, got {value!r}.")


def _positive_int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


def _installed_version(distribution_name: str) -> str:
    try:
        return version(distribution_name)
    except PackageNotFoundError as exc:
        raise DoclingDeploymentError(
            f"Required package {distribution_name!r} is not installed."
        ) from exc


def _validate_artifacts_path(runtime: DoclingRuntimeSettings) -> Path | None:
    if runtime.offline and not runtime.artifacts_path:
        raise DoclingDeploymentError(
            "DOCLING_OFFLINE=true requires DOCLING_ARTIFACTS_PATH."
        )
    if not runtime.artifacts_path:
        return None
    artifacts_path = Path(runtime.artifacts_path)
    try:
        if not artifacts_path.is_dir():
            raise DoclingDeploymentError(
                "DOCLING_ARTIFACTS_PATH must point to a directory containing "
                "downloaded Docling models."
            )
        artifact_files = [
            path
            for path in artifacts_path.rglob("*")
            if path.is_file()
        ]
    except OSError as exc:
        raise DoclingDeploymentError(
            "DOCLING_ARTIFACTS_PATH cannot be inspected."
        ) from exc
    if not artifact_files:
        raise DoclingDeploymentError(
            "DOCLING_ARTIFACTS_PATH does not contain downloaded Docling models."
        )
    has_model_file = any(
        path.suffix.casefold() in _MODEL_ARTIFACT_SUFFIXES
        for path in artifact_files
    )
    has_config_file = any(
        path.name.casefold() in {"config.json", "preprocessor_config.json"}
        for path in artifact_files
    )
    if not has_model_file or not has_config_file:
        raise DoclingDeploymentError(
            "DOCLING_ARTIFACTS_PATH is missing a Docling model weight or config "
            "file. Run 'docling-tools models download' with the same Docling "
            "version and preserve its directory structure."
        )
    return artifacts_path


def _is_docling_deployment_failure(
    error: BaseException,
    runtime: DoclingRuntimeSettings,
) -> bool:
    """Identify model/configuration failures that must never use a PDF fallback."""
    if isinstance(error, (ModuleNotFoundError, ImportError, FileNotFoundError, PermissionError)):
        return True
    message = " ".join(str(error).casefold().split())
    deployment_markers = (
        "model.safetensors",
        "missing safe tensors",
        "missing model",
        "missing config",
        "artifact",
        "checkpoint",
        "weights",
        "downloads disabled",
        "download model",
        "huggingface",
        "repository",
    )
    if any(marker in message for marker in deployment_markers):
        return True
    return bool(runtime.offline and isinstance(error, (OSError, RuntimeError)))


def validate_docling_runtime(
    runtime: DoclingRuntimeSettings | None = None,
) -> str:
    """Fail fast for package/configuration problems before a job is claimed."""
    runtime = runtime or DoclingRuntimeSettings.from_env()
    try:
        import_module("docling.document_converter")
        import_module("docling.datamodel.pipeline_options")
        import_module("docling_core.types.doc")
    except (ModuleNotFoundError, ImportError) as exc:
        raise DoclingDeploymentError(
            f"Docling runtime import failed: {type(exc).__name__}: {exc}"
        ) from exc
    docling_version = _installed_version("docling")
    _validate_artifacts_path(runtime)
    if runtime.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    return docling_version


def _safe_fallback_reason(prefix: str, error: BaseException | None = None) -> str:
    if error is None:
        reason = prefix
    else:
        message = " ".join(str(error).split())
        reason = f"{prefix}: {type(error).__name__}"
        if message:
            reason = f"{reason}: {message}"
    return reason[:_MAX_FALLBACK_REASON_LENGTH]


def _log_context_values(
    pdf_path: str,
    log_context: Mapping[str, Any] | None,
) -> tuple[str, str, str]:
    context = log_context or {}
    return (
        str(context.get("document_id") or "unknown"),
        str(context.get("document_version_id") or "unknown"),
        Path(pdf_path).name,
    )


def _processing_metadata(
    *,
    extractor: str,
    extractor_version: str,
    fallback_used: bool,
    fallback_reason: str | None,
    ocr_used: bool,
) -> dict[str, Any]:
    return {
        "extractor": extractor,
        "extractor_version": extractor_version,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "ocr_used": ocr_used,
    }


# ---------- 1. 텍스트 추출 (docling 레이아웃 모델) ----------
def _needs_ocr(pdf_path: str, empty_page_ratio: float = 0.5) -> bool:
    """텍스트 없는 페이지 비율로 스캔본 여부를 빠르게 판단."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    empty_page_count = sum(1 for page in doc if len(page.get_text().strip()) < 10)
    total_pages = len(doc)
    doc.close()
    return total_pages > 0 and (empty_page_count / total_pages) > empty_page_ratio


def _build_converter(
    do_ocr: bool,
    runtime: DoclingRuntimeSettings,
) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    artifacts_path = _validate_artifacts_path(runtime)
    if artifacts_path is not None:
        pipeline_options.artifacts_path = artifacts_path
    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=runtime.num_threads,
        device=AcceleratorDevice(runtime.accelerator_device),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def _table_item_to_block(item: Any) -> dict | None:
    """
    표를 {header, rows} 블록으로 변환. 마크다운 텍스트 대신 docling의 grid 구조(병합 셀이
    이미 각 칸에 채워져 있음)를 직접 읽어서, "모델명"이 두 하위 컬럼을 묶는 경우처럼
    헤더가 여러 줄에 걸친 표도 헤더 전체를 놓치지 않도록 한다.
    """
    grid = item.data.grid
    if not grid:
        return None

    def row_to_line(row) -> str:
        return "| " + " | ".join((cell.text or "").strip() for cell in row) + " |"

    header_lines = []
    body_lines = []
    still_header = True
    for row in grid:
        line = row_to_line(row)
        if still_header and any(cell.column_header for cell in row):
            header_lines.append(line)
        else:
            still_header = False
            if line.strip("| "):
                body_lines.append(line)

    if not header_lines:
        # column_header 플래그가 없는 표는 첫 행을 헤더로 취급 (기존 동작 유지)
        if not body_lines:
            return None
        header_lines, body_lines = [body_lines[0]], body_lines[1:]

    if not body_lines:
        return None
    return {"type": "table", "header": "\n".join(header_lines), "rows": body_lines}


def _push_header(stack: list[tuple[int, str]], level: int, text: str) -> list[str]:
    """
    제목 스택에 (레벨, 텍스트)를 쌓아 상위 제목이 남도록 관리한다.
    현재 레벨 이상인 항목은 하위 제목이 끝난 것으로 보고 스택에서 제거한다.
    반환값은 최상위->현재 순서의 section_path (예: ["3. 안전", "3.2 전원 차단"]).
    """
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, text))
    return [t for _, t in stack]


def extract_sections_with_docling(
    pdf_path: str,
    runtime: DoclingRuntimeSettings,
) -> tuple[list[dict[str, Any]], bool]:
    """
    docling으로 문서를 파싱해 SECTION_HEADER/TITLE 라벨을 기준으로 섹션을 묶는다.
    각 섹션은 본문/표를 구분한 blocks 리스트를 가지며, 표는 나중에 행 단위로 청킹하기 위해
    header/rows를 분리해서 보관한다 (문장 중간이 아니라 표 중간에서 잘리는 것을 막기 위함).
    section_path는 제목 레벨을 스택으로 추적해 상위 제목까지 포함한 경로로 남긴다.
    """
    from docling_core.types.doc import TableItem

    do_ocr = _needs_ocr(pdf_path)
    if do_ocr:
        logger.info(
            "Docling OCR enabled filename=%s",
            Path(pdf_path).name,
        )

    converter = _build_converter(do_ocr, runtime)
    doc = converter.convert(
        pdf_path,
        max_file_size=runtime.max_file_bytes,
        max_num_pages=runtime.max_pages,
    ).document

    sections = []
    header_stack: list[tuple[int, str]] = []
    current_section_path = ["머리말"]
    current_start_page = None
    buf_blocks: list[dict] = []
    buf_pages: list[int] = []

    def flush():
        if buf_blocks:
            sections.append({
                "header": current_section_path[-1],
                "section_path": list(current_section_path),
                "start_page": current_start_page or 1,
                "end_page": buf_pages[-1] if buf_pages else (current_start_page or 1),
                "blocks": buf_blocks,
            })

    for item, level in doc.iterate_items():
        label = str(getattr(item, "label", "") or "")
        if label in _SKIP_LABELS:
            continue

        page_no = item.prov[0].page_no if getattr(item, "prov", None) else None

        if label in _HEADER_LABELS:
            flush()
            text = (getattr(item, "text", "") or "").strip()
            if text:
                current_section_path = _push_header(header_stack, level, text)
            current_start_page = page_no
            buf_blocks = []
            buf_pages = []
            continue

        if isinstance(item, TableItem):
            block = _table_item_to_block(item)
        else:
            text = (getattr(item, "text", "") or "").strip()
            block = {"type": "text", "text": text} if text else None
        if block is None:
            continue

        if current_start_page is None:
            current_start_page = page_no
        buf_blocks.append(block)
        buf_pages.append(page_no or current_start_page)

    flush()
    return sections, do_ocr


def _merge_vertical_fragments(lines: list[dict], x_tol: float = 1.5, y_tol: float = 3.0) -> list[dict]:
    """
    도면/회로도 라벨이 세로로 한 글자씩 쌓여 있으면(예: '주'/'회'/'로') PyMuPDF가 이를
    별개의 줄로 뽑아낸다. 같은 페이지에서 x좌표가 거의 같고 위 줄 바로 아래에 다음 줄이
    붙어 있는 짧은 줄들을 순서대로 이어붙여 원래 단어('주회로')로 복원한다.
    """
    merged = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        run = [cur]
        if len(cur["text"]) <= 2 and cur.get("bbox"):
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                prev_bbox, nxt_bbox = run[-1]["bbox"], nxt.get("bbox")
                if (
                    len(nxt["text"]) <= 2
                    and nxt_bbox
                    and nxt["page"] == cur["page"]
                    and abs(nxt_bbox[0] - prev_bbox[0]) < x_tol
                    and abs(nxt_bbox[1] - prev_bbox[3]) < y_tol
                ):
                    run.append(nxt)
                    j += 1
                else:
                    break
        if len(run) >= 2:
            merged.append({
                "text": "".join(l["text"] for l in run),
                "page": cur["page"],
                "is_header": False,
            })
            i += len(run)
        else:
            merged.append({"text": cur["text"], "page": cur["page"], "is_header": cur["is_header"]})
            i += 1
    return merged


def extract_sections_with_pymupdf(pdf_path: str, header_min_size: float = 8.8) -> list[dict]:
    """
    docling 변환이 실패할 때 쓰는 폴백. 폰트 크기/굵기로 헤더를 추정하는 예전 방식이라
    docling만큼 정확하지도, 표를 구조적으로 인식하지도 못하지만, 문서 전체를 못 쓰게 되는
    상황(예: docling-parse가 특정 폰트에서 UnicodeDecodeError로 죽는 경우)은 막아준다.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    raw_lines = []
    for pno, page in enumerate(doc):
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                line_text = "".join(s["text"] for s in spans).strip()
                if not line_text:
                    continue
                max_size = max(s["size"] for s in spans)
                is_bold = any("Bold" in s["font"] for s in spans)
                is_symbol_only = bool(re.fullmatch(r"[❶-❾■□•\-\d\s\.\)]+", line_text))
                is_header = (
                    is_bold
                    and max_size >= header_min_size
                    and 1 < len(line_text) <= 30
                    and not is_symbol_only
                )
                raw_lines.append({
                    "text": line_text,
                    "page": pno + 1,
                    "is_header": is_header,
                    "bbox": line.get("bbox"),
                })
    doc.close()

    lines = _merge_vertical_fragments(raw_lines)

    sections = []
    current_header = "머리말"
    current_start_page = lines[0]["page"] if lines else 1
    buf: list[dict] = []

    def flush():
        if buf:
            sections.append({
                "header": current_header,
                "section_path": [current_header],
                "start_page": current_start_page,
                "end_page": buf[-1]["page"],
                "blocks": [{"type": "text", "text": b["text"]} for b in buf],
            })

    for l in lines:
        if l["is_header"]:
            flush()
            current_header = l["text"]
            current_start_page = l["page"]
            buf = []
        else:
            buf.append(l)
    flush()
    return sections


def extract_sections(
    pdf_path: str,
    *,
    runtime: DoclingRuntimeSettings | None = None,
    log_context: Mapping[str, Any] | None = None,
) -> SectionExtractionResult:
    """Prefer Docling and only fall back for document-specific failures."""
    runtime = runtime or DoclingRuntimeSettings.from_env()
    path = Path(pdf_path)
    file_size = path.stat().st_size
    if file_size > runtime.max_file_bytes:
        raise PdfProcessingLimitError(
            f"PDF file size {file_size} exceeds limit {runtime.max_file_bytes}."
        )
    import fitz  # PyMuPDF

    with fitz.open(path) as pdf:
        page_count = len(pdf)
    if page_count > runtime.max_pages:
        raise PdfProcessingLimitError(
            f"PDF page count {page_count} exceeds limit {runtime.max_pages}."
        )

    document_id, version_id, filename = _log_context_values(pdf_path, log_context)
    try:
        docling_version = validate_docling_runtime(runtime)
    except DoclingDeploymentError as exc:
        if runtime.required:
            raise
        if not runtime.allow_pymupdf_fallback:
            raise DoclingConversionError(
                "Docling is unavailable and PyMuPDF fallback is disabled."
            ) from exc
        fallback_reason = _safe_fallback_reason("Docling unavailable", exc)
        logger.warning(
            "PDF extraction fallback document_id=%s document_version_id=%s "
            "filename=%s extractor=pymupdf reason=%s",
            document_id,
            version_id,
            filename,
            fallback_reason,
        )
        return SectionExtractionResult(
            sections=extract_sections_with_pymupdf(pdf_path),
            processing_metadata=_processing_metadata(
                extractor="pymupdf",
                extractor_version=_installed_version("PyMuPDF"),
                fallback_used=True,
                fallback_reason=fallback_reason,
                ocr_used=False,
            ),
        )

    try:
        sections, ocr_used = extract_sections_with_docling(pdf_path, runtime)
    except (ModuleNotFoundError, ImportError) as exc:
        raise DoclingDeploymentError(
            f"Docling runtime import failed during conversion: {type(exc).__name__}: {exc}"
        ) from exc
    except Exception as exc:
        if _is_docling_deployment_failure(exc, runtime):
            raise DoclingDeploymentError(
                _safe_fallback_reason("Docling model/configuration failed", exc)
            ) from exc
        if not runtime.allow_pymupdf_fallback:
            raise DoclingConversionError(
                _safe_fallback_reason("Docling conversion failed", exc)
            ) from exc
        fallback_reason = _safe_fallback_reason("Docling conversion failed", exc)
        logger.exception(
            "PDF extraction fallback document_id=%s document_version_id=%s "
            "filename=%s extractor=pymupdf reason=%s",
            document_id,
            version_id,
            filename,
            fallback_reason,
        )
        return SectionExtractionResult(
            sections=extract_sections_with_pymupdf(pdf_path),
            processing_metadata=_processing_metadata(
                extractor="pymupdf",
                extractor_version=_installed_version("PyMuPDF"),
                fallback_used=True,
                fallback_reason=fallback_reason,
                ocr_used=False,
            ),
        )
    if not sections:
        if not runtime.allow_pymupdf_fallback:
            raise DoclingConversionError("Docling returned no sections.")
        fallback_reason = _safe_fallback_reason("Docling returned no sections")
        logger.warning(
            "PDF extraction fallback document_id=%s document_version_id=%s "
            "filename=%s extractor=pymupdf reason=%s",
            document_id,
            version_id,
            filename,
            fallback_reason,
        )
        return SectionExtractionResult(
            sections=extract_sections_with_pymupdf(pdf_path),
            processing_metadata=_processing_metadata(
                extractor="pymupdf",
                extractor_version=_installed_version("PyMuPDF"),
                fallback_used=True,
                fallback_reason=fallback_reason,
                ocr_used=False,
            ),
        )
    return SectionExtractionResult(
        sections=sections,
        processing_metadata=_processing_metadata(
            extractor="docling",
            extractor_version=docling_version,
            fallback_used=False,
            fallback_reason=None,
            ocr_used=ocr_used,
        ),
    )


# ---------- 2. 노이즈 제거 ----------
def clean_text(text: str) -> str:
    """페이지 구분자, 저작권 푸터, 페이지번호 단독줄, 깨진 줄바꿈, 아이콘 폰트 오매핑 정리."""
    text = text.replace("\x0c", "").replace("\ufeff", "")
    # 아이콘 폰트가 유니코드 사용자 정의 영역(Private Use Area)에 매핑되는 경우 제거
    text = re.sub(r"[-]", "", text)
    # 인증마크 아이콘 폰트가 필리핀 고문자(Tagalog) 유니코드 영역에 잘못 매핑되는 경우 제거
    text = re.sub(r"[\u1700-\u1753]", "", text)
    # 저작권 푸터 라인 제거
    text = re.sub(r"©\s*Copyright Reserved.*", "", text)
    # docling이 워터마크/저작권 폰트를 깨진 글자로 디코딩하는 경우 제거
    # (예: "-_Transparent GuiGe_-", ")_SUDmroDUdms값*thcd_)")
    text = re.sub(r"[-)_]*Transparent Gui\w*[-_]*", "", text)
    text = re.sub(r"\)?_SUDmroDUdms\S*\)", "", text)
    # 목차 점선 리더 제거 (예: "제품 구입 감사 안내문 ........... 3")
    text = re.sub(r"\.{5,}", " ", text)
    # 마크다운 표 구분선 제거 (예: "|------|------|") - 내용 없이 글자 수만 차지
    text = re.sub(r"^\|?[\s\-:|]+\|[\s\-:|]*$\n?", "", text, flags=re.MULTILINE)
    # 페이지번호만 있는 줄 제거 (예: " 4  " 또는 "1")
    lines = text.split("\n")
    lines = [l for l in lines if not re.fullmatch(r"\s*\d{1,4}\s*", l)]
    text = "\n".join(lines)
    # 과도한 빈 줄 정리
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_blocks(blocks: list[dict]) -> list[dict]:
    """섹션의 각 블록(본문/표)에 clean_text를 적용하고 빈 블록은 제거."""
    cleaned = []
    for b in blocks:
        if b["type"] == "text":
            text = clean_text(b["text"])
            if text:
                cleaned.append({"type": "text", "text": text})
        else:
            header = clean_text(b["header"])
            rows = [clean_text(r) for r in b["rows"]]
            rows = [r for r in rows if r]
            if header and rows:
                cleaned.append({"type": "table", "header": header, "rows": rows})
    return cleaned


# ---------- 3. 불필요 섹션 제외 ----------
def filter_sections(sections: list[dict], exclude_keywords: list[str]) -> list[dict]:
    """외형치수도, 모터특성도 등 도면/그래프 계열 섹션 제거."""
    kept = []
    for s in sections:
        header = s["header"] or ""
        if any(kw in header for kw in exclude_keywords):
            continue
        kept.append(s)
    return kept


# ---------- 4. 청킹 ----------
def _split_sentence_units(text: str) -> list[str]:
    """
    문장 끝 부호(.!?) 또는 줄바꿈 뒤에서 나누되, 구분자를 포함시켜 이어붙이면 원문이 그대로 복원되게 한다.
    "10. 점검 및 유지보수"처럼 숫자 뒤의 마침표(번호 매기기)는 문장 종결로 보지 않는다.
    """
    parts = re.split(r"(\n+|(?<=[.!?])(?<!\d[.!?])\s+)", text)
    units = []
    for i in range(0, len(parts), 2):
        unit = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        if unit:
            units.append(unit)
    return units


def _validate_chunk_options(chunk_size: int, overlap: int) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between zero and chunk_size - 1")


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """긴 단일 문장·표 행도 임베딩 제한을 넘지 않도록 최종 안전 분할한다."""
    if len(text) <= chunk_size:
        return [text] if text else []

    step = chunk_size - overlap
    return [
        part
        for start in range(0, len(text), step)
        if (part := text[start : start + chunk_size].strip())
    ]


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> list[str]:
    """문장 경계를 우선하되 모든 청크가 chunk_size 이내가 되도록 묶는다."""
    _validate_chunk_options(chunk_size, overlap)
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    units = _split_sentence_units(text)
    chunks = []
    current: list[str] = []
    current_len = 0

    def flush():
        if current:
            chunks.append("".join(current).strip())

    for unit in units:
        if current and current_len + len(unit) > chunk_size:
            flush()
            # 겹침(overlap): 직전 청크의 끝 문장들을 이어서 다음 청크 앞부분에 유지
            carry: list[str] = []
            carry_len = 0
            for u in reversed(current):
                if carry_len + len(u) > overlap:
                    break
                carry.insert(0, u)
                carry_len += len(u)
            current = carry
            current_len = carry_len

        current.append(unit)
        current_len += len(unit)

    flush()
    return [
        bounded_chunk
        for chunk in chunks
        for bounded_chunk in _hard_split(chunk, chunk_size, overlap)
    ]


def chunk_table(block: dict, chunk_size: int = 600) -> list[str]:
    """표를 행(row) 단위로 묶어서 청킹. 각 청크 맨 앞에 헤더 행을 반복해 어떤 컬럼인지 알 수 있게 한다."""
    _validate_chunk_options(chunk_size, 0)
    header = block["header"]
    chunks = []
    group: list[str] = []
    group_len = len(header)

    for row in block["rows"]:
        if group and group_len + len(row) + 1 > chunk_size:
            chunks.append("\n".join([header, *group]))
            group = []
            group_len = len(header)
        group.append(row)
        group_len += len(row) + 1

    if group:
        chunks.append("\n".join([header, *group]))
    return [
        bounded_chunk
        for chunk in chunks
        for bounded_chunk in _hard_split(chunk, chunk_size, 0)
    ]


def chunk_section(blocks: list[dict], chunk_size: int = 600, overlap: int = 100) -> list[str]:
    """
    섹션의 blocks를 청킹. 표는 행 중간에서 잘리지 않도록 chunk_table로 별도 처리하고,
    연속된 본문 블록은 하나로 합쳐서 기존 chunk_text로 처리한다.
    """
    chunks = []
    text_run: list[str] = []

    def flush_text_run():
        if text_run:
            chunks.extend(chunk_text("\n".join(text_run), chunk_size, overlap))
            text_run.clear()

    for block in blocks:
        if block["type"] == "table":
            flush_text_run()
            chunks.extend(chunk_table(block, chunk_size))
        else:
            text_run.append(block["text"])

    flush_text_run()
    return chunks


# ---------- 5. 메타데이터 부여 + 전체 조립 (docs/preprocessing-contract.md 규격) ----------
def process_pdf(
    pdf_path: str,
    product_type: str,
    model_name: str,
    manufacturer: str = "오토닉스",
    exclude_sections: list[str] | None = None,
    chunk_size: int = 600,
    overlap: int = 100,
    source_type: str = "manual",
    document_type_code: str = "equipment_manual",
    access_level: str = "restricted",
    docling_settings: DoclingRuntimeSettings | None = None,
    log_context: Mapping[str, Any] | None = None,
) -> dict:
    """
    PDF 한 개를 받아서 docs/preprocessing-contract.md 규격의
    {"document": {...}, "chunks": [...], "processing_metadata": {...}}를 반환.

    access_level 기본값은 "restricted"로 둔다. 제조사 매뉴얼의 재배포 조건이
    확인되기 전까지는 공개로 단정하지 않기 위함 (architecture.md 데이터 계층 정책 참고).
    """
    if exclude_sections is None:
        exclude_sections = ["외형치수도", "모터 특성도", "제품 특성 데이터", "판넬 가공 치수도",
                             "모터특성도", "TYPICAL", "검출 영역", "검출 재질별",
                             "Table of Content", "목차", "차례"]

    doc_name = Path(pdf_path).stem
    external_id = f"{source_type}:{manufacturer}:{model_name}:{doc_name}"
    file_sha256 = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()

    doc_metadata = {
        "manufacturer": manufacturer,
        "model_number": model_name,
        "product_type": product_type,
    }

    document = {
        "external_id": external_id,
        "title": doc_name,
        "source_type": source_type,
        "document_type_code": document_type_code,
        "publisher": manufacturer,
        "access_level": access_level,
        "file_sha256": file_sha256,
        "metadata": doc_metadata,
    }

    extraction = extract_sections(
        pdf_path,
        runtime=docling_settings,
        log_context=log_context,
    )
    sections = extraction.sections
    for s in sections:
        s["blocks"] = clean_blocks(s["blocks"])

    sections = filter_sections(sections, exclude_sections)

    chunks = []
    for s in sections:
        text_chunks = chunk_section(s["blocks"], chunk_size, overlap)
        if s["start_page"] == s["end_page"]:
            page_fields = {"page_number": s["start_page"]}
        else:
            page_fields = {"page_start": s["start_page"], "page_end": s["end_page"]}

        for chunk in text_chunks:
            content = chunk.strip()
            if not content:
                continue
            chunks.append({
                "document_external_id": external_id,
                "chunk_index": len(chunks),
                **page_fields,
                "section_path": s["section_path"],
                "content": content,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "metadata": doc_metadata,
                "embedding_status": "pending",
            })

    return {
        "document": document,
        "chunks": chunks,
        "processing_metadata": extraction.processing_metadata,
    }


if __name__ == "__main__":
    # 간단한 자체 테스트
    print("pdf_pipeline 모듈 로드 확인 완료. process_pdf() 함수를 임포트해서 사용하세요.")
