# SafeMaint 가짜 PDF RAG·LLM E2E 검증 보고서

- 검증 일시: 2026-07-20 (KST)
- 작업 경로: `C:\Users\Chan\Desktop\3차프로젝트_코드`
- 시작 브랜치: `feature/chan`
- 시작 HEAD: `1bece3d`
- 데이터 안전 범위: 이름이 `_test`로 끝나는 격리 DB와 매 실행 고유 테스트 volume만 사용

이 보고서에는 API 키, 세션 토큰, 비밀번호, 실제 회사 PDF 또는 고객사 데이터를 기록하지 않았다.

## 1. 현재 브랜치와 시작 HEAD

작업은 `feature/chan`, 커밋 `1bece3d`에서 시작했다. 시작 시 이미 존재한 `docs` 삭제 3건, `outputs/`, `scripts/generate-db-erd.py`는 사용자 변경으로 판단하여 수정하거나 커밋 대상에 포함하지 않는다.

## 2. 기존에 구현되어 있던 기능

- PostgreSQL 16, pgvector, SQLAlchemy, Alembic, 멱등 seed
- 사원번호 기반 등록·로그인·세션 Bearer 토큰과 RBAC 테이블
- 인증된 PDF 업로드 API, 문서 버전, SHA-256, 비동기 처리 작업 생성
- 공통 `process_pdf()` 기반 PDF 추출·청킹과 Worker 재시도
- BAAI/bge-m3 1024차원 임베딩과 pgvector 저장
- 하이브리드 검색, rerank, 파일명·페이지·섹션·버전·검색점수 출처
- OpenAI 호환 LLM provider 추상화와 회사 문서 외부 전송 차단 기반

## 3. 새로 구현하거나 수정한 기능

- 업로드 API가 회사 범위 문서 유형과 `restricted`/`private`만 허용하도록 제한
- `public_guide`/`public` 위장 등록 차단
- `document.approve` 권한 기반 승인 API와 트랜잭션 승인 서비스 추가
- 승인 시 row lock, 소속·상태 검증, 이전 버전 superseded, current version 갱신, 감사 이벤트 기록
- 익명/로그인 사용자를 구분하는 선택 인증 dependency 추가
- 로그인 사용자 역할·권한·`UserSite`에서 신뢰 가능한 RAG 접근 범위를 서버가 생성
- `document_manager`/`admin`은 전 사업장, 일반 `document.read` 사용자는 배정 사업장만 허용
- private 문서는 별도 정책이 없어 계속 차단
- 선택 문서 ID와 선택 버전 ID를 권한 범위 안에서 교집합 제한으로 적용
- 회사 문서 질의는 외부 분석·답변 provider를 호출하지 않도록 사전 차단
- RAG 호스트 포트의 개발 오버레이 노출 제거(Compose 내부 통신만 사용)
- 업로드 메타데이터의 `model_number`를 Worker 모델명 메타데이터에 반영
- PyMuPDF 가짜 PDF 생성부터 실제 BGE-M3 검색까지 자동 E2E 스크립트 추가

## 4. 변경 파일 목록

- `backend/app/api/deps.py`
- `backend/app/api/routes/chat.py`
- `backend/app/api/routes/documents.py`
- `backend/app/schemas/documents.py`
- `backend/app/services/chat.py`
- `backend/app/services/document_approval.py`
- `backend/tests/test_chat_api.py`
- `backend/tests/test_database_integration.py`
- `backend/tests/test_deps.py`
- `backend/tests/test_documents_api.py`
- `ai/rag_service/retrieval.py`
- `ai/rag_service/worker.py`
- `ai/tests/test_retrieval_integration.py`
- `ai/tests/test_pdf_rag_llm_e2e.py`
- `docker-compose.dev.yml`
- `docker-compose.rag.yml`
- `docker-compose.e2e.yml`
- `scripts/run-pdf-rag-e2e.ps1`
- `README.md`
- `reports/pdf_rag_llm_e2e_report.md`

## 5. 실행한 주요 명령

```powershell
python -m compileall backend\app ai\rag_service ai\tests
python -m pytest backend\tests
python -m pytest ai\tests
docker compose -f docker-compose.yml -f docker-compose.e2e.yml config --quiet
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-pdf-rag-e2e.ps1
git diff --check
```

DB 통합 테스트는 `RUN_DB_INTEGRATION=1`, `ALLOW_TEST_DB_MUTATION=1`과 `_test` DB를 사용했다. 실제 BGE-M3 E2E는 스크립트가 별도 Compose project와 고유 DB·문서 volume에서 실행했다.

## 6. 테스트 결과

| 검증 | 결과 | 비고 |
|---|---:|---|
| Python compileall | PASS | backend/RAG/test 모듈 구문 확인 |
| Backend 기본 테스트 | PASS | 60 passed, 13 skipped |
| AI 기본 테스트 | PASS | 23 passed, 6 skipped |
| Backend 격리 DB 통합 테스트 | PASS | 73 passed |
| RAG 격리 DB 통합 테스트 | PASS | 27 passed, 2 skipped |
| Compose E2E config | PASS | 공통 + E2E 오버레이 병합 성공 |
| 실제 PDF/Worker/BGE-M3/RAG E2E | PASS | 1 passed |
| 외부 GPT-4o-mini E2E | SKIP | 명시 플래그/API 키 없이 외부 호출하지 않음 |
| 가짜 PDF 렌더링 육안 검사 | PASS | 2개 PDF 모두 텍스트 잘림·겹침 없이 표시 |

## 7. PDF 업로드 결과

- 무토큰 업로드: HTTP 401
- 기본 worker 사용자 업로드: HTTP 403
- document_manager 업로드: HTTP 201
- 회사 문서를 공용 유형으로 위장한 업로드: HTTP 422
- TEST-LC-100 최초/재업로드: version 1, version 2 생성
- TEST-CV-200 업로드: 별도 문서/version 1 생성
- 각 파일의 실제 SHA-256, 원본 파일명, 저장 경로, 처리 작업 생성을 검증
- 바이너리 PDF는 테스트 중 생성하고 저장소에는 커밋하지 않음

## 8. Worker 처리 결과

- 모든 대상 작업이 `queued → processing → completed`를 거쳐 완료
- 문서 버전이 `pending → processing → review_required`로 전환
- 오류 메시지 없음, 시도 횟수 1~3 범위
- 청크 1개 이상, content hash, 페이지 범위, 섹션 경로 존재
- 임베딩 상태 `ready`, 모델 `BAAI/bge-m3`, 차원 1024
- 제조사·제품군·모델 메타데이터 존재
- TEST-LC-100/E2E-37과 TEST-CV-200/E2E-99가 서로 다른 문서 청크에 저장됨
- 현재 이미지에 Docling 부가 모듈이 없어 PyMuPDF 폴백 경로가 사용됐고 정상 처리됨

## 9. BGE-M3 검색 결과

- `retrieval_mode=hybrid`
- 승인 전 문서, 검토 대기 버전, superseded 이전 버전은 검색되지 않음
- 승인된 current version만 검색됨
- 선택한 document/version과 결과의 ID·버전 일치
- 출처에 원본 파일명, 페이지 1, 섹션, similarity, retrieval score, reranker score 존재
- 익명 Backend 채팅은 public-only
- 인증 document_manager 채팅은 서버가 계산한 회사 범위로 검색
- `allow_company=false` 또는 다른 문서 선택 시 TEST-LC-100 회사 문서 미검색

## 10. 라이트커튼/컨베이어 오검색 여부

라이트커튼 질의의 최상위 근거는 TEST-LC-100이며 E2E-37 청크가 사용됐다. 선택 필터를 적용한 질의에서 TEST-CV-200/E2E-99가 답 또는 최상위 근거로 사용되지 않았다. 테스트 중 발견한 `document_id OR version_id` 결합 오류는 각각 독립된 제한 조건으로 수정하여, 둘 다 주어지면 교집합만 검색하도록 회귀 검증했다.

## 11. GPT-4o-mini 답변 결과

실제 외부 GPT 호출은 기본 비활성화 정책과 명시 플래그 부재로 SKIP했다. 공개 가짜 PDF만 사용하는 외부 E2E 테스트 코드는 준비되어 있으며, 실행 시 최대 답변 호출 1회만 수행하도록 분석 결과를 미리 제공한다. API 키가 없을 때 나머지 로컬 E2E는 실패하지 않고 완료된다.

## 12. 회사 문서 외부 전송 차단 결과

- 회사 범위 검색이 가능한 요청은 외부 상황 분석기 호출 전부터 차단
- 회사 출처가 포함되면 외부 답변 생성도 차단
- `generation_mode=template` 또는 로컬 fallback을 사용하면서 검색 출처 유지
- fake provider 테스트에서 외부 호출 횟수 0 검증
- 클라이언트의 접근 범위 위조는 무시하고 세션 사용자 DB 권한으로 재계산

## 13. 남은 문제와 주의사항

- 실제 GPT-4o-mini 네트워크 호출은 테스트용 API 키와 명시 플래그로 별도 실행해야 한다.
- 실제 회사 문서와 restricted 문서를 외부 LLM 테스트에 사용하면 안 된다.
- Docling 전체 경로를 검증하려면 현재 RAG 이미지에 선택 의존성을 추가해야 하며, 지금은 검증된 PyMuPDF 폴백을 사용한다.
- 스캔 PDF는 OCR 엔진이 준비되기 전까지 `ocr_required`로 남는다.
- 테스트 DB·문서 volume은 개발/운영 volume 보호를 위해 자동 삭제하지 않는다. 서비스는 종료되고 성공한 fixture 데이터·파일은 정리되지만 반복 실행 후 남은 테스트 전용 volume은 관리자가 이름을 확인해 별도 정리해야 한다.
- 프런트엔드 업로드·승인 관리 UI는 이번 범위에 포함하지 않았다.

## 14. 사용자가 직접 재실행할 명령

외부 호출 없이 전체 로컬 PDF·BGE-M3 E2E:

```powershell
Set-Location "C:\Users\Chan\Desktop\3차프로젝트_코드"
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-pdf-rag-e2e.ps1
```

공개 가짜 PDF의 GPT-4o-mini 근거 답변까지 선택 실행:

```powershell
$env:OPENAI_API_KEY = "발급받은_테스트용_키"
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-pdf-rag-e2e.ps1 `
  -RunExternalLlm
Remove-Item Env:OPENAI_API_KEY
```

Qwen3 OpenAI 호환 서버로 전환할 때는 provider 코드를 바꾸지 않고 `LLM_BASE_URL`, `LLM_ANALYZER_MODEL`, `LLM_ANSWER_MODEL`만 변경한다.
