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

chunks = process_pdf(
    pdf_path="ai/data/raw/manual.pdf",
    product_type="포토센서",
    model_name="PQ Series",
    manufacturer="오토닉스",
)
```

각 결과에는 고유한 `chunk_id`, 본문 `text`, 문서명·제조사·제품군·모델명·챕터·페이지 `metadata`가 포함됩니다. 긴 문장과 표도 설정한 `chunk_size`를 넘지 않도록 최종 분할합니다.

## 테스트

개인 PDF 없이도 장치 선택, 청크 길이 제한, 반복 섹션 ID 중복 방지를 확인할 수 있습니다.

```powershell
.\ai\.venv\Scripts\python.exe -m pytest ai\tests -q
```

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
사고사례 기반 참고자료이며 작업 승인이나 제조사 정비 매뉴얼을 대체하지 않습니다.
