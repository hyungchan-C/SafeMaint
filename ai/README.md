# PDF 전처리 파이프라인

`ai/preprocessing/pdf_pipeline.py`는 제조사 PDF를 Docling으로 분석하고 임베딩 직전 JSON 청크로 변환합니다. NVIDIA GPU가 있으면 자동으로 사용하고, 없으면 CPU를 선택합니다. Docling 패키지·오프라인 모델 누락은 배포 오류로 처리하며, Docling이 정상 설치된 상태에서 특정 PDF 변환만 실패한 경우에만 설정에 따라 PyMuPDF 폴백을 사용합니다.

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
result["processing_metadata"]  # extractor, 버전, 폴백 및 OCR 정보
```

반환값은 기존 `{"document": {...}, "chunks": [...]}` 계약을 유지하면서 `processing_metadata`를 추가합니다. `document`에는 `external_id`, `source_type`, `access_level`, `file_sha256` 등이, 각 `chunk`에는 `document_external_id`, `chunk_index`, `content`, `content_hash`, `section_path`, `embedding_status` 등이 포함됩니다. 긴 문장과 표도 설정한 `chunk_size`를 넘지 않도록 최종 분할합니다.

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

`embed_pending_chunks()`는 처리 범위를 호출자가 명시하도록 일반화돼 있습니다.
매뉴얼을 적재한 뒤에는 생성된 `document_id`만 지정해 기존 pending 청크와 섞이지
않게 임베딩하세요. `source_type`을 지정하지 않은 전체 pending 처리는 CLI의
`--all-pending`를 명시한 경우에만 허용됩니다.

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

사고 데이터 적재 스크립트는 DB 이름이 `_test`로 끝나지 않으면 실제 적재를
거부합니다. 범용 임베딩·검색 명령은 운영 매뉴얼도 처리할 수 있으므로 아래와 같이
처리 범위를 반드시 명시해야 합니다. 전체 9,223개 문서와 12,296개 청크는 이
실험에서 적재하지 않습니다.

```powershell
# 사고 데이터만 임베딩
python -m app.commands.embed_document_chunks `
  --source-type incident --limit 500

# 매뉴얼만 임베딩
python -m app.commands.embed_document_chunks `
  --source-type manual --limit 500

# 특정 업로드 문서만 임베딩
python -m app.commands.embed_document_chunks `
  --document-id "문서 UUID"

# 명시적으로 모든 pending 청크 처리
python -m app.commands.embed_document_chunks `
  --all-pending --limit 500

# 사고와 매뉴얼을 함께 검색
python -m app.commands.search_document_chunks `
  --source-type incident --source-type manual `
  --query "컨베이어 베어링 교체" --top-k 5
```

`--source-type`과 `--document-id`를 함께 지정하면 두 조건의 교집합만 처리합니다.
범위 옵션 없이 실행하거나 전체 옵션을 필터와 함께 사용하면 CLI가 실행을
거부합니다. 새 PDF 업로드 흐름에서는 manual 전체가 아니라 생성된 document ID만
전달해야 합니다.

### 프런트 채팅과 BGE-M3 검색 연결

위 실험으로 `safemaint_rag_test`에 임베딩이 준비된 뒤 다음 개발용 overlay를
사용합니다. 무거운 Sentence Transformers 의존성은 기존 backend 이미지가 아닌
별도 `rag` 서비스에만 설치됩니다.

```powershell
docker compose `
  --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.dev.yml `
  -f docker-compose.rag.yml `
  up -d --build
```

처음 실행할 때 RAG 이미지 빌드와 BGE-M3 로딩에 시간이 걸릴 수 있습니다.

```powershell
Invoke-RestMethod http://localhost:8010/health/ready

$body = @{
  question = "컨베이어밸트 베어링을 교체하려고 합니다"
  context = @{ equipment_name = "컨베이어 CV-203"; component_name = "베어링" }
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/v1/chat `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

응답의 `retrieval_mode`이 `bge-m3`이면 벡터 검색이 연결된 상태이고,
`safety-fallback`이면 RAG 서비스 또는 격리 DB를 확인해야 합니다. 검색 결과는
설정한 source type의 참고자료이며 작업 승인이나 현장 안전관리자의 판단을
대체하지 않습니다.
