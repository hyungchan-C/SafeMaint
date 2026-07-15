"""
재사용 가능한 PDF -> 청크 파이프라인

지금(오토닉스 4개 파일 배치 처리)과 나중(F-01 서비스 내 업로드 기능)에
동일하게 쓰기 위해 함수 단위로 분리했습니다.

텍스트/구조 추출은 docling의 레이아웃 모델을 사용합니다. 폰트 크기 같은
문서별 매직넘버 튜닝 없이도 제조사가 다른 PDF에 일반적으로 적용하기 위함입니다.
GPU(CUDA)가 있으면 자동으로 사용합니다.

사용법:
    from pdf_pipeline import process_pdf
    chunks = process_pdf(
        pdf_path="파일경로.pdf",
        product_type="포토센서",
        model_name="BTS Series",
        manufacturer="오토닉스",  # 기본값이라 생략 가능
        exclude_sections=["외형치수도", "모터 특성도", "제품 특성 데이터"],
    )
"""

import fitz  # PyMuPDF (스캔본 여부 판단용)
import re
import json
from pathlib import Path

from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    AcceleratorOptions,
    AcceleratorDevice,
)
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import TableItem

_HEADER_LABELS = {"section_header", "title"}
_SKIP_LABELS = {"picture", "page_header", "page_footer"}


# ---------- 1. 텍스트 추출 (docling 레이아웃 모델) ----------
def _needs_ocr(pdf_path: str, empty_page_ratio: float = 0.5) -> bool:
    """텍스트 없는 페이지 비율로 스캔본 여부를 빠르게 판단."""
    doc = fitz.open(pdf_path)
    empty_page_count = sum(1 for page in doc if len(page.get_text().strip()) < 10)
    total_pages = len(doc)
    doc.close()
    return total_pages > 0 and (empty_page_count / total_pages) > empty_page_ratio


def _build_converter(do_ocr: bool) -> DocumentConverter:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=8, device=AcceleratorDevice.CUDA
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def _table_item_to_block(item: TableItem) -> dict | None:
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


def extract_sections_with_docling(pdf_path: str) -> list[dict]:
    """
    docling으로 문서를 파싱해 SECTION_HEADER/TITLE 라벨을 기준으로 섹션을 묶는다.
    각 섹션은 본문/표를 구분한 blocks 리스트를 가지며, 표는 나중에 행 단위로 청킹하기 위해
    header/rows를 분리해서 보관한다 (문장 중간이 아니라 표 중간에서 잘리는 것을 막기 위함).
    """
    do_ocr = _needs_ocr(pdf_path)
    if do_ocr:
        print(f"[정보] {pdf_path}: 텍스트 없는 페이지 비율이 높아 OCR을 사용합니다 (느려질 수 있음).")

    converter = _build_converter(do_ocr)
    doc = converter.convert(pdf_path).document

    sections = []
    current_header = "머리말"
    current_start_page = None
    buf_blocks: list[dict] = []
    buf_pages: list[int] = []

    def flush():
        if buf_blocks:
            sections.append({
                "header": current_header,
                "start_page": current_start_page or 1,
                "end_page": buf_pages[-1] if buf_pages else (current_start_page or 1),
                "blocks": buf_blocks,
            })

    for item, _level in doc.iterate_items():
        label = str(getattr(item, "label", "") or "")
        if label in _SKIP_LABELS:
            continue

        page_no = item.prov[0].page_no if getattr(item, "prov", None) else None

        if label in _HEADER_LABELS:
            flush()
            current_header = (getattr(item, "text", "") or "").strip() or current_header
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
    return sections


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


def extract_sections(pdf_path: str) -> list[dict]:
    """docling 추출을 우선 시도하고, 라이브러리 자체 문제로 실패하면 PyMuPDF 폰트 휴리스틱으로 재시도."""
    try:
        return extract_sections_with_docling(pdf_path)
    except Exception as exc:
        print(f"[경고] {pdf_path}: docling 변환 실패({exc}). PyMuPDF 폴백으로 재시도합니다.")
        return extract_sections_with_pymupdf(pdf_path)


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


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> list[str]:
    """섹션 내부 텍스트를 문장 단위로 chunk_size에 맞춰 묶는다 (문장 중간에서 잘리지 않도록)."""
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
    return [c for c in chunks if c]


def chunk_table(block: dict, chunk_size: int = 600) -> list[str]:
    """표를 행(row) 단위로 묶어서 청킹. 각 청크 맨 앞에 헤더 행을 반복해 어떤 컬럼인지 알 수 있게 한다."""
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
    return chunks


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


# ---------- 5. 메타데이터 부여 + 전체 조립 ----------
def process_pdf(
    pdf_path: str,
    product_type: str,
    model_name: str,
    manufacturer: str = "오토닉스",
    exclude_sections: list[str] = None,
    chunk_size: int = 600,
    overlap: int = 100,
) -> list[dict]:
    """PDF 한 개를 받아서 임베딩 직전 형태의 청크 리스트를 반환."""
    if exclude_sections is None:
        exclude_sections = ["외형치수도", "모터 특성도", "제품 특성 데이터", "판넬 가공 치수도",
                             "모터특성도", "TYPICAL", "검출 영역", "검출 재질별",
                             "Table of Content", "목차", "차례"]

    doc_name = Path(pdf_path).stem

    sections = extract_sections(pdf_path)
    for s in sections:
        s["blocks"] = clean_blocks(s["blocks"])

    sections = filter_sections(sections, exclude_sections)

    all_chunks = []
    for s in sections:
        text_chunks = chunk_section(s["blocks"], chunk_size, overlap)
        for idx, chunk in enumerate(text_chunks):
            if not chunk.strip():
                continue
            all_chunks.append({
                "chunk_id": f"{doc_name}_{s['header']}_{idx}".replace(" ", "_"),
                "text": chunk,
                "metadata": {
                    "문서명": doc_name,
                    "제조사": manufacturer,
                    "제품군": product_type,
                    "모델명": model_name,
                    "챕터": s["header"],
                    "페이지": f"{s['start_page']}-{s['end_page']}",
                },
            })

    return all_chunks


if __name__ == "__main__":
    # 간단한 자체 테스트
    print("pdf_pipeline 모듈 로드 확인 완료. process_pdf() 함수를 임포트해서 사용하세요.")
