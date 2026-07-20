# SafeMaint 사고사례 기본 테이블 전처리 v10

국내재해사례 API와 사고사망 API를 전체 수집·전처리하여 **현재 단계에 필요한 기본 테이블만** 생성합니다.

```text
output/
├─ domestic_cases.csv
├─ domestic_cases.jsonl
├─ fatal_cases.csv
├─ fatal_cases.jsonl
└─ manifest.json
```

`accident_documents`, `accident_chunks`, 임베딩은 법령·안전문서·매뉴얼 등 다른 데이터 전처리가 모두 끝난 뒤 통합 단계에서 한 번만 생성합니다.

## 수정 원칙

1. v9의 기존 전처리 함수 `app/preprocess.py`는 그대로 고정했습니다.
2. 새 날짜·위치·본문 보완은 `app/postprocess.py`의 독립 후처리 단계에서만 수행합니다.
3. 후처리가 변경할 수 있는 컬럼은 아래 6개뿐입니다.

```text
accident_date_text
location
location_sido
location_sigungu
location_detail
content_text
```

4. `boardno`, `business`, `detailed_business`, `causal_object`, `title`, `title_raw`, `content_raw` 등 보호 컬럼은 전체 회귀검사로 변경 0건을 확인합니다.
5. 오류가 하나라도 남거나 API 필수 컬럼이 바뀌면 `preprocessing_ready=false` 또는 실행 실패로 종료합니다.

## 주요 처리 기준

### domestic_cases

- `boardno` 기준 중복 제거
- `keyword` → `title`, `contents` → `content_raw`
- HTML·이미지·스크립트·엔티티 제거 및 공백 정리
- 문자열 `null`, `none`, `nan`, 빈 문자열 → `content_text=NULL`
- 본문의 명시적 업종·기인물만 `detailed_business`, `causal_object`로 추출
- 사고일이 확실한 완전한 날짜만 `YYYY-MM-DD`
- 작성일·게시일·설치일·등록일·행사일·파일명 날짜는 사고일에서 제외
- `소재지`, `지역`, `발생장소`, 사고서술의 명확한 위치를 추출
- 사고지와 소속업체 주소가 함께 있으면 사고지 우선
- 시설명·업종명 일부를 행정구역으로 저장하지 않음
- 과거 행정구역·반복 오타는 별칭 사전으로 처리하고, 하위 위치가 불명확하면 확실한 상위 위치까지만 저장
- 세미나·자료모음 등 비사고 게시물은 사고 위치를 저장하지 않음

### fatal_cases

- 제목의 `[날짜, 지역] 제목` 구조 분리
- 본문의 연도 포함 실제 사고일을 우선하여 `YYYY-MM-DD`
- 제목 위치와 본문 위치를 각각 검증
- 제목 오타는 유효한 본문 위치로 보정
- 소속업체 주소보다 사고 발생 장소를 우선
- `경기광주`, `광주 능평동` 등 반복 표기를 정규화
- 시설·업종·공사명 일부를 상세 위치로 저장하지 않음
- 제목과 본문이 서로 다른 유효 지역이면 위치 컬럼을 NULL
- HTML 잔여문자·제어문자·명확한 붙어쓰기만 안전하게 정리

## API 변경 방어

`app/schema_guard.py`가 모든 API 항목에 필수 컬럼이 있는지 검사합니다.

```text
domestic: boardno, business, keyword, contents
fatal: keyword, contents
```

새 게시물이 추가되는 것은 처리하지만, 컬럼명이 바뀌거나 필수 컬럼이 빠지면 잘못된 파일을 만들지 않고 즉시 실패합니다.

## 공통 컬럼

두 테이블의 처음 9개 컬럼은 이름·순서·형식이 같습니다.

```text
id
title
accident_date_text
location
location_sido
location_sigungu
location_detail
content_text
content_raw
```

## 실행 후 독립 검증

전체 실행 후 다음 명령을 반드시 실행합니다.

```bat
python tools\verify_output.py output
```

`"passed": true`, `"error_count": 0`이면 출력 파일의 컬럼, 행 수, ID, 중복, 날짜, 위치 구성, HTML·제어문자, 후처리 재실행 일치, CSV·JSONL 일치가 모두 통과한 것입니다.

자세한 실행 방법은 `RUN_WINDOWS.txt`, 검증 범위는 `VERIFICATION.md`와 `REFERENCE_AUDIT.json`을 확인합니다.
