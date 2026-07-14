# SafeMaint DB 1단계 설계

## 핵심 원칙

- PostgreSQL 16과 pgvector 0.8.2를 사용합니다.
- 모든 운영 스키마 변경은 버전이 있는 Alembic 마이그레이션으로 적용합니다.
- UUID 기본키와 timezone 포함 시각을 사용합니다.
- 평가 당시 입력, 규칙 버전, likelihood, severity, score, risk level을 저장해 이후 규칙 변경이 과거 결과를 바꾸지 않게 합니다.
- 초기 seed에는 기준 코드만 포함하며 가짜 사업장이나 개인정보를 넣지 않습니다.
- Docker named volume은 설치와 업데이트 사이에 계속 유지합니다.

## 테이블과 관계

```text
sites 1 ── N equipment 1 ── N components
  │              │                │
  └──────────── assessments ──────┘
                    │
                    ├── N assessment_hazards
                    ├── N checklist_items
                    └── N assessment_evidence N ── 1 document_chunks N ── 1 documents

reference_codes  (평가 상태·위험등급·사고유형·작업유형·에너지원)
audit_events     (평가 생성 등 변경 이력)
```

| 테이블 | 주요 내용 |
|---|---|
| `sites` | 사업장 코드·이름·활성 상태 |
| `equipment` | 사업장별 설비, 제조사·모델 |
| `components` | 설비별 부품, 부품번호 |
| `assessments` | 입력 스냅샷, 상태, 엔진·규칙 버전, 검토 정보 |
| `assessment_hazards` | 평가 당시 위험요인과 1~4 가능성·중대성, 계산 점수 |
| `checklist_items` | 순서가 있는 TBM 항목과 완료 상태 |
| `documents` | 문서 출처·버전·접근등급·해시·메타데이터 |
| `document_chunks` | 페이지·본문·해시·메타데이터·임베딩 상태 |
| `assessment_evidence` | 평가와 검색 청크 사이의 순위·점수·사용 여부 |
| `reference_codes` | 멱등 seed 대상 최소 기준 코드 |
| `audit_events` | append-only 방식으로 사용할 변경 이력 |

## 임베딩 후속 전략

사용할 임베딩 모델이 확정되지 않았으므로 `document_chunks.embedding`은 차원을 지정하지 않은 `vector`이고, 모델명과 실제 차원을 별도 컬럼에 기록합니다. 현 단계에서는 HNSW 인덱스를 만들지 않습니다.

모델을 확정한 뒤에는 다음 순서로 별도 Alembic 마이그레이션을 만듭니다.

1. 기존 청크의 임베딩 모델·차원 혼재 여부 확인
2. 목표 차원이 고정된 새 벡터 컬럼 또는 검증 제약 추가
3. 백필과 검증
4. 데이터 규모와 조회 패턴을 측정한 뒤 HNSW/IVFFlat 인덱스 추가

## Docker 실행 순서

`docker-compose.yml`은 다음 조건을 강제합니다.

1. `db`가 `pg_isready` healthcheck를 통과합니다.
2. `migrate`가 `alembic upgrade head`를 실행하고 종료 코드 0으로 끝납니다.
3. `seed`가 PostgreSQL `ON CONFLICT` upsert를 실행하고 종료 코드 0으로 끝납니다.
4. `backend`가 시작되고 DB 준비 상태를 확인합니다.
5. `frontend`가 시작됩니다.

최초 DB 초기화 스크립트와 Alembic 모두 `CREATE EXTENSION IF NOT EXISTS vector`를 사용합니다. 따라서 기존 볼륨 때문에 `/docker-entrypoint-initdb.d`가 재실행되지 않아도 마이그레이션이 확장을 확인합니다.

## 통합 테스트

반드시 버려도 되는 별도 DB를 사용하고 이름을 `_test`로 끝내야 합니다.

```powershell
$env:DATABASE_URL="postgresql+psycopg://safemaint:테스트비밀번호@127.0.0.1:55432/safemaint_test"
$env:RUN_DB_INTEGRATION="1"
$env:ALLOW_TEST_DB_MUTATION="1"
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m pytest backend\tests\test_database_integration.py
```

테스트는 pgvector 확장, Alembic head, 주요 테이블·제약조건, seed 멱등성, 평가 저장·재조회, DB 대시보드 집계를 확인합니다.

## 데이터 보존 주의

- 기본 볼륨명: `safemaint_postgres_data`
- 개발 override 기본 볼륨명: `safemaint_postgres_dev_data`
- `docker compose down`은 컨테이너와 네트워크만 정리하고 볼륨은 유지합니다.
- `docker compose down -v`, `docker volume rm`, 수동 DB 삭제는 사용하지 않습니다.
- 운영 투입 전에는 2단계 백업·복구 스크립트와 실제 복구 훈련을 완료해야 합니다.
