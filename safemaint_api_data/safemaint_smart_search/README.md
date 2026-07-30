# SafeMaint AI 안전보건법령 스마트검색 수집기

## 이번 작업 범위

한국산업안전보건공단 안전보건법령 스마트검색 API를 `category=0`으로 호출해 법령, 고시·훈령·예규, 안전보건 미디어, KOSHA GUIDE 등을 검색·수집하고 CSV/JSONL로 저장합니다.

이번 코드에는 다음 기능이 없습니다.

- 이전 국내재해·사고사망 데이터 사용 또는 수정
- 다른 데이터와 통합
- `rag_documents` 생성
- `rag_chunks` 생성
- 청킹
- 임베딩
- pgvector 적재
- LLM·RAG 검색

추후 기존 전처리 테이블과 이번 테이블을 별도 통합 코드에서 `rag_documents`와 `rag_chunks`로 공통화할 수 있도록 결정적 ID, 원본 ID, 문서 유형, 정제 본문, 출처 경로, 검색어-문서 연결을 보존합니다.

## 핵심 구현

- 모든 요청은 `category=0`
- 결과 문서의 `category`를 `category_code`, `category_name`, `source_type`으로 저장
- `response.body.items.item`과 `response.body.total_media` 모두 처리
- 검색어 기본 목록을 문서에 적힌 순서대로 코드에 포함
- 중복 검색어는 `normalized_term` 기준 제거
- 기본 목록에는 `벨트 장력 조절`이 두 번 있으므로 코드에는 두 위치 모두 포함하지만 실제 등록 검색어는 144개
- TXT·CSV로 신규 검색어 추가
- 기존 output을 먼저 읽고 중복 없이 이어서 수집
- 페이지별 `status=success`만 완료로 간주
- 결정적 UUID5 ID 사용
- 문서 고유 기준: `category_code + source_doc_id`
- hit 고유 기준: `term_id + document_id`
- 각 성공 페이지 직후 8개 파일 모두 atomic write
- API 한도 초과, max_requests, Ctrl+C, 오류 시 현재 데이터 보존
- HTTP 500/502/503/504, 타임아웃, 연결 오류, JSON 파싱 실패는 2·4·8초 간격으로 최대 3회 재시도
- Encoding/Decoding 서비스키 중복 URL 인코딩 방지
- CSV: `utf-8-sig`
- JSONL: `utf-8`, `ensure_ascii=False`
- pandas 미사용, 표준 라이브러리와 requests만 사용

## 출력 테이블

### smart_search_terms

```text
term_id
term
normalized_term
term_type
priority
term_source
is_active
created_at
updated_at
```

### smart_search_runs

```text
run_id
term_id
search_value
request_category
page_no
num_of_rows
total_count
total_pages
associated_words_json
category_count_json
result_code
result_msg
http_status
status
error_message
requested_at
completed_at
elapsed_seconds
```

### smart_search_documents

```text
document_id
source_doc_id
category_code
category_name
source_type
title
content_raw
content_clean
keyword_raw
keyword_clean
filepath
image_path_json
med_thumb_yn
media_style
content_hash
first_seen_at
last_seen_at
updated_at
```

### smart_search_hits

```text
hit_id
run_id
term_id
document_id
result_group
rank_no
score
matched_content_raw
matched_content_clean
highlight_content_raw
highlight_content_clean
collected_at
updated_at
```

## 추후 RAG 통합 시 활용

```text
smart_search_documents.document_id   → rag_documents의 원본 문서 연결 ID
smart_search_documents.source_type   → 출처 유형
smart_search_documents.source_doc_id → API 원본 문서 ID
smart_search_documents.title         → 문서 제목
smart_search_documents.content_clean → 청킹할 정제 본문
smart_search_documents.content_raw   → 원문 보존
category/filepath/keyword            → metadata_json
smart_search_hits                    → 검색어별 점수·강조 내용·연결 근거
```

이번 단계에서는 위 변환을 실행하지 않습니다.

자세한 실행 명령은 `RUN_WINDOWS.txt`를 확인하세요.
