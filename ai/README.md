# PDF 전처리 파이프라인

`ai/preprocessing/pdf_pipeline.py`는 제조사 PDF를 Docling으로 분석하고 임베딩 직전 JSON 청크로 변환합니다. NVIDIA GPU가 있으면 자동으로 사용하고, 없으면 CPU를 선택합니다. Docling 변환 자체가 실패하면 PyMuPDF 폴백을 사용합니다.

## 개발 환경 준비

프로젝트 루트에서 AI 전용 가상환경을 만듭니다.

```powershell
python -m venv ai\.venv
.\ai\.venv\Scripts\python.exe -m pip install -r ai\requirements-dev.txt
```

원본 PDF는 `ai/data/raw/`, 생성한 JSON은 `ai/data/processed/`에 둡니다. 두 폴더는 Git에서 제외되므로 실제 매뉴얼과 결과 파일을 커밋하지 않습니다.

## 실행

오토닉스 배치 예시:

```powershell
.\ai\.venv\Scripts\python.exe ai\preprocessing\run_autonics_pipeline.py
```

다른 코드에서 재사용할 때는 `process_pdf()`를 호출합니다.

```python
from ai.preprocessing.pdf_pipeline import process_pdf

result = process_pdf(
    pdf_path="ai/data/raw/manual.pdf",
    product_type="포토센서",
    model_name="PQ Series",
    manufacturer="오토닉스",
)
result["document"]  # 문서 레코드 1건
result["chunks"]    # 청크 레코드 N건
```

반환값은 `docs/preprocessing-contract.md` 규격을 따르는 `{"document": {...}, "chunks": [...]}`입니다. `document`에는 `external_id`, `source_type`, `access_level`, `file_sha256` 등이, 각 `chunk`에는 `document_external_id`, `chunk_index`, `content`, `content_hash`, `section_path`, `embedding_status` 등이 포함됩니다. 긴 문장과 표도 설정한 `chunk_size`를 넘지 않도록 최종 분할합니다.

## 테스트

개인 PDF 없이도 장치 선택, 청크 길이 제한, 반복 섹션 ID 중복 방지를 확인할 수 있습니다.

```powershell
.\ai\.venv\Scripts\python.exe -m pytest ai\tests -q
```
