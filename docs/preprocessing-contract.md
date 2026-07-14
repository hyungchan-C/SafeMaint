# 전처리 데이터 입력 규격 v0.1

전처리 담당자는 문서 1건과 청크 N건을 아래 논리 규격으로 전달합니다. 현재는 파일 교환 규격이며, 2단계 적재 API 또는 배치 로더가 같은 필드를 사용합니다.

## 문서 레코드

| 필드 | 타입 | 필수 | 규칙 |
|---|---|---:|---|
| `external_id` | string(200) | 예 | 데이터 공급 단계에서 영구적으로 유일한 ID |
| `title` | string(500) | 예 | 원문 제목 |
| `source_type` | string(50) | 예 | `regulation`, `kosha_guide`, `manual`, `site_rule`, `incident` 등 |
| `publisher` | string(200) | 아니요 | 발행기관·제조사 |
| `source_url` | string | 아니요 | 접근 가능한 원문 주소 |
| `revision` | string(100) | 아니요 | 개정번호·문서 버전 |
| `published_at` | `YYYY-MM-DD` | 아니요 | 발행일 |
| `access_level` | enum | 예 | `public`, `restricted`, `private` |
| `file_sha256` | hex string(64) | 권장 | 원본 파일 바이트 SHA-256 |
| `metadata` | object | 예 | 설비·제조사·모델·부품·작업·에너지원 필터 값 |

## 청크 레코드

| 필드 | 타입 | 필수 | 규칙 |
|---|---|---:|---|
| `document_external_id` | string(200) | 예 | 상위 문서의 `external_id` |
| `chunk_index` | integer | 예 | 문서 안에서 0부터 시작, 문서별 유일 |
| `page_number` | integer | 아니요 | 단일 페이지 청크일 때 1부터 시작 |
| `page_start` / `page_end` | integer | 아니요 | 여러 페이지에 걸친 청크 범위 |
| `section_path` | string[] | 예 | 예: `["3. 안전", "3.2 전원 차단"]` |
| `content` | string | 예 | 정규화된 청크 본문, 빈 문자열 금지 |
| `content_hash` | hex string(64) | 예 | 정규화 본문 UTF-8 SHA-256 |
| `metadata` | object | 예 | 문서 메타데이터를 상속하고 청크별 값을 추가 |
| `embedding` | float[] | 아니요 | 모델 확정 전에는 전달하지 않음 |
| `embedding_model` | string | 조건부 | 임베딩이 있으면 필수 |
| `embedding_dimension` | integer | 조건부 | 임베딩 배열 길이와 동일해야 함 |
| `embedding_status` | enum | 예 | 기본 `pending`; `ready`, `failed`, `skipped` 가능 |

## JSONL 예시

```json
{"document_external_id":"manual:maker-a:cv203:r1","chunk_index":0,"page_number":12,"page_start":12,"page_end":12,"section_path":["안전","전원 차단"],"content":"정비 전 주전원을 차단하고 잠금장치를 설치한다.","content_hash":"<64자리 sha256>","metadata":{"manufacturer":"maker-a","model_number":"CV-203","component_type":"belt","task_types":["inspection","jam_removal"],"energy_sources":["electric","mechanical"]},"embedding_status":"pending"}
```

## 전달 전 검증

- 파일 인코딩은 UTF-8, JSONL 1줄당 청크 1건으로 통일합니다.
- 같은 문서 안에서 `chunk_index`가 중복되면 안 됩니다.
- 페이지 값은 1 이상이고 `page_end >= page_start`여야 합니다.
- 원문에서 찾을 수 없는 내용을 생성하거나 요약으로 덧붙이지 않습니다.
- 개인정보·고객사 비밀정보는 `access_level`을 지정하고 공개 Git에 올리지 않습니다.
- 임베딩 모델이 확정되기 전에는 전처리팀이 임의 차원의 벡터를 만들지 않습니다.
