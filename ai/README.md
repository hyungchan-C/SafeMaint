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

## DB 적재 (`ai/embeddings/`)

`ai/embeddings/ingest_pdf.py`의 `ingest_pdf()`는 `process_pdf()` 결과를 그대로
`documents`/`document_chunks`에 저장합니다. **여기서는 임베딩을 만들지 않습니다** —
청크는 `embedding_status="pending"` 상태로만 적재되고, 실제 벡터 생성은
`backend/app/services/document_embeddings.py`의 `embed_pending_chunks()`가
별도 배치로 처리합니다(적재와 임베딩을 분리한 구조, 팀원의 임베딩 구현체를
그대로 재사용).

DB 스키마·세션은 backend의 SQLAlchemy 모델(`app.db.models.document`)을 그대로
가져다 쓰므로(`db_loader.py`), `ai/requirements.txt`에 `SQLAlchemy`/`psycopg`/
`pgvector`가 추가돼 있고, `DATABASE_URL` 환경변수(backend와 동일한 규칙)로
접속할 Postgres가 필요합니다.

```python
from ai.embeddings.ingest_pdf import ingest_pdf

result = ingest_pdf(
    pdf_path="ai/data/raw/manual.pdf",
    product_type="포토센서",
    model_name="PQ Series",
    manufacturer="오토닉스",
)
result["document_id"]  # 적재된 documents.id (UUID)
result["chunk_count"]  # 적재된 청크 수
```

> ⚠️ 현재 `embed_pending_chunks()`는 `Document.source_type == "incident"`로
> 필터링돼 있어, PDF 매뉴얼(`source_type="manual"`)로 적재된 청크는 이 함수가
> 아직 집어가지 않습니다. 실제로 임베딩이 채워지려면 이 필터를 일반화하는
> 작업이 팀원 쪽에 필요합니다.

## BGE-M3 임베딩 실험

PDF 전처리 환경과 분리된 선택 가상환경을 사용합니다. 메인 백엔드 requirements에는
Torch와 Sentence Transformers를 추가하지 않습니다.

```powershell
python -m venv ai\.venv-embedding
.\ai\.venv-embedding\Scripts\python.exe -m pip install `
  -r ai\requirements-embedding.txt
```

NVIDIA GPU를 사용할 때는 `ai/requirements-embedding.txt` 하단의 공식 PyTorch
CUDA wheel 설치 예시를 추가로 실행합니다. 모델은 `ai/.model-cache/`에 저장되며
Git에 포함되지 않습니다.

격리 DB 생성, dry-run, domestic/fatal 각 100개 적재, 멱등성 확인, 최대 500개
임베딩 및 5개 질의 검색은 다음 스크립트로 재실행할 수 있습니다.

```powershell
.\scripts\run-rag-experiment.ps1
```

스크립트와 Python 명령은 DB 이름이 `_test`로 끝나지 않으면 실제 적재를 거부합니다.
전체 9,223개 문서와 12,296개 청크는 이 실험에서 적재하지 않습니다.
