# SafeMaint AI

제조설비 정비작업 전에 작업자가 입력한 설비·부품·작업 정보를 바탕으로 위험요인과 안전조치의 초안을 만들고, 관련 문서 근거와 역할별 TBM 체크리스트를 제공하는 안전관리 지원 서비스입니다.

> SafeMaint AI의 결과는 위험성평가 초안입니다. 실제 작업의 최종 판단과 승인은 반드시 안전관리자가 수행해야 합니다.

## 현재 MVP 범위

- 사업장 1곳과 컨베이어 설비를 기준으로 한 초기 구조
- 제조사·모델·부품번호·작업 종류·에너지원·자연어 설명 입력
- 규칙 기반 위험도 미리보기
- 향후 하이브리드 검색과 Reranker를 연결할 서비스 인터페이스
- 작업 현황, 위험도, 체크리스트를 보여주는 대시보드 UI
- FastAPI 자동 API 문서와 기본 테스트
- PostgreSQL 16 + pgvector 0.8.2 영구 저장소
- Alembic 마이그레이션과 멱등 기준 데이터 seed
- 평가 결과 트랜잭션 저장과 실제 DB 대시보드 집계

초기 MVP에서는 사진 인식, VLM, GPS·동선 추적, 자체 LLM 파인튜닝, 복잡한 승인 시스템을 제외합니다. 검색 성능이 확보된 뒤 우선순위에 따라 추가합니다.

## 기술 구성

| 영역 | 기술 |
|---|---|
| 프론트엔드 | Next.js, React, TypeScript |
| 백엔드 | FastAPI, Pydantic, SQLAlchemy 2, psycopg 3, Alembic |
| 데이터베이스 | PostgreSQL 16, pgvector 0.8.2 |
| 목표 검색 구조 | 메타데이터 필터 + BM25 + BGE-M3 + Reranker |
| 목표 생성 구조 | 파운데이션 모델 + 검색 근거 기반 답변 |
| 위험도 | 규칙 기반 위험성평가 엔진 |

자세한 구조는 [docs/architecture.md](docs/architecture.md)를 참고하세요.

## 폴더 구조

```text
SafeMaint/
├─ backend/             FastAPI API, 위험도 규칙, ORM, Alembic
├─ frontend/            안전관리 대시보드
├─ docs/                설계 문서
├─ infra/postgres/init/ pgvector 최초 부트스트랩
├─ .env.example         환경변수 예시
├─ docker-compose.yml   공통·고객사 실행 환경(DB 포트 비공개)
└─ docker-compose.dev.yml 로컬 DB 포트 override
```

DB 테이블과 관계는 [docs/database.md](docs/database.md), 전처리 결과 입력 규격은 [docs/preprocessing-contract.md](docs/preprocessing-contract.md)를 참고하세요.

## 가장 간단한 전체 실행

Docker Desktop을 실행한 뒤 최초 한 번 `.env`를 만듭니다. `POSTGRES_PASSWORD`와 `DATABASE_URL` 안의 비밀번호는 반드시 같은 긴 임의 문자열로 변경해야 합니다.

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up --build -d
docker compose ps
```

Compose 프로젝트 이름을 `safemaint`로 고정했으므로 한글 폴더명에서 `invalid reference format`이 발생하지 않습니다. 실행 순서는 다음과 같습니다.

```text
db healthy → migrate 완료 → seed 완료 → backend healthy → frontend
```

- 대시보드: <http://localhost:3000>
- API 문서: <http://localhost:8000/docs>
- 준비 상태: <http://localhost:8000/health/ready>

기본 구성은 PostgreSQL 포트를 PC 외부나 호스트에 공개하지 않습니다. 로컬 DB 도구로 접속할 때만 개발 override를 함께 사용합니다.

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

> `docker compose down -v`는 DB 영구 볼륨을 삭제하므로 사용하지 마세요.

## 백엔드 로컬 개발

필수 환경은 Python 3.13 이상, Node.js 22.13 이상입니다.

DB만 Docker에서 실행하고 FastAPI는 로컬에서 실행하는 방법입니다. `.env`의 DB 비밀번호와 아래 `DATABASE_URL` 비밀번호를 일치시키세요.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements-dev.txt

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
$env:DATABASE_URL="postgresql+psycopg://safemaint:설정한비밀번호@127.0.0.1:5432/safemaint"
python -m alembic -c backend\alembic.ini upgrade head
$env:PYTHONPATH="backend"
python -m app.db.seed
python -m uvicorn app.main:app --app-dir backend --reload
```

- 생존 확인: <http://localhost:8000/health/live>
- DB 준비 확인: <http://localhost:8000/health/ready>
- API 문서: <http://localhost:8000/docs>

SQLAlchemy는 현재 규칙 엔진·검색 서비스가 동기 인터페이스이고 초기 MVP 트래픽이 크지 않다는 점을 기준으로 동기 세션을 사용합니다. 구현 복잡도와 트랜잭션 경계를 단순하게 유지하고, 실제 부하 측정에서 DB 대기 병목이 확인될 때 비동기 전환을 검토합니다.

## 프론트엔드 로컬 개발

새 터미널에서 실행합니다.

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

- 대시보드: <http://localhost:3000>

## DB 마이그레이션과 업데이트

스키마는 `Base.metadata.create_all()`이 아니라 Alembic으로만 변경합니다.

```powershell
python -m alembic -c backend\alembic.ini revision --autogenerate -m "변경 내용"
python -m alembic -c backend\alembic.ini upgrade head
```

프로그램 업데이트 시 새 코드를 받은 뒤 같은 실행 명령을 사용하면 기존 named volume은 유지되고 새로운 마이그레이션과 seed만 적용됩니다.

```powershell
git pull
docker compose up --build -d
```

현재 1단계에서는 DB 계층·자동 마이그레이션·seed·저장 API까지 구현했습니다. 운영용 `install.ps1`, `update.ps1`, `backup.ps1`, 복구 검증과 외부 DB 전용 Compose 프로필은 2단계에서 추가합니다. 그 전에는 고객사 실데이터를 넣지 마세요.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/assessments/preview` | DB 저장 없는 기존 규칙 기반 미리보기 |
| `POST` | `/api/v1/assessments` | 평가·위험요인·체크리스트·감사 이벤트 트랜잭션 저장 |
| `GET` | `/api/v1/assessments/{id}` | 저장된 평가 조회 |
| `GET` | `/api/v1/dashboard/summary` | 실제 DB 기반 오늘 작업·고위험·검토·체크리스트 집계 |

## 테스트

```powershell
python -m pytest backend\tests
Set-Location frontend
npm.cmd run typecheck
npm.cmd run build
```

DB 통합 테스트는 실수로 운영 DB를 수정하지 않도록 DB 이름이 `_test`로 끝나고 두 개의 명시적 허용 환경변수가 모두 설정된 경우에만 실행됩니다. 자세한 실행 예시는 [docs/database.md](docs/database.md)에 있습니다.

## 협업 규칙

- 기본 브랜치: `main`
- 기능 개발: `feature/기능명`
- 버그 수정: `fix/수정명`
- 기능별 Pull Request를 생성하고 검토 후 병합합니다.
- `.env`, 사업장 내부 문서, 제조사 재배포 제한 자료, 개인정보, 모델 파일은 커밋하지 않습니다.
