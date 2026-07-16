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
| 문서 전처리 | Docling, PyMuPDF |
| 목표 검색 구조 | 메타데이터 필터 + BM25 + BGE-M3 + Reranker |
| 목표 생성 구조 | 파운데이션 모델 + 검색 근거 기반 답변 |
| 위험도 | 규칙 기반 위험성평가 엔진 |

자세한 구조는 [docs/architecture.md](docs/architecture.md)를 참고하세요.

## 폴더 구조

```text
SafeMaint/
├─ backend/             FastAPI API, 위험도 규칙, ORM, Alembic
├─ frontend/            안전관리 대시보드
├─ ai/                  PDF 추출·정제·청킹 파이프라인
├─ docs/                설계 문서
├─ infra/postgres/init/ pgvector 최초 부트스트랩
├─ scripts/             팀원 개발환경 설정·DB 검증 스크립트
├─ .env.example         환경변수 예시
├─ docker-compose.yml   공통·고객사 실행 환경(DB 포트 비공개)
└─ docker-compose.dev.yml 로컬 DB 포트 override
```

DB 테이블과 관계는 [docs/database.md](docs/database.md), 전처리 결과 입력 규격은 [docs/preprocessing-contract.md](docs/preprocessing-contract.md), 실행 방법은 [ai/README.md](ai/README.md)를 참고하세요.

사고 데이터 샘플 적재와 BGE-M3/pgvector 실험은 운영·개발 DB가 아닌 이름이
`_test`로 끝나는 격리 DB에서만 실행합니다. 상세 준비와 재실행 명령은
[ai/README.md](ai/README.md)의 **BGE-M3 임베딩 실험**을 참고하세요.

채팅 화면에서 실제 BGE-M3 검색과 사고사례 출처를 사용하려면 샘플 실험 완료 후
RAG Compose 구성을 함께 실행합니다.

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.dev.yml `
  -f docker-compose.rag.yml `
  up -d --build
```

RAG 서비스가 실행되지 않거나 검색 DB가 준비되지 않은 경우에도 채팅 API는 작업
키워드에 맞는 공통 안전수칙을 반환하며, 화면에 근거 검색이 연결되지 않았다는
경고를 표시합니다. 화면에서 선택한 매뉴얼은 아직 파일 업로드·전처리 대상이
아니므로 사고사례 검색 근거와 구분됩니다.

## 팀원 로컬 DB 온보딩

각 팀원은 Git, Docker Desktop, DBeaver를 설치하고 Docker Desktop을 실행한 상태에서 시작합니다. Docker DB는 팀원 PC마다 독립적으로 생성됩니다.

### `dev` 브랜치 받기

현재 팀 통합 개발 코드는 `dev` 브랜치에 있습니다. 저장소를 새로 받는 팀원은 다음과 같이 clone합니다.

```powershell
git clone --branch dev https://github.com/hyungchan-C/SafeMaint.git
Set-Location SafeMaint
```

이미 저장소를 clone했지만 로컬에 `dev`가 없는 팀원은 다음 명령으로 원격 브랜치를 연결합니다.

```powershell
git fetch origin
git switch --track origin/dev
```

이미 `dev`를 사용 중인 팀원은 다음 명령으로 최신 내용을 받습니다.

```powershell
git switch dev
git pull origin dev
```

기능 브랜치는 검토 후 `dev`에 병합하고, 배포 준비가 끝난 통합 버전만 `main`으로 승격합니다.

### 권장 실행: DB·백엔드·프론트엔드 전체 Docker

저장소를 clone한 직후 프로젝트 루트에서 실행합니다.

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env`에서 `POSTGRES_PASSWORD`를 12자 이상의 영문·숫자·하이픈·밑줄 조합으로 변경하고, `DATABASE_URL` 안의 비밀번호도 똑같이 변경합니다. 예시 비밀번호를 그대로 사용하면 설정 스크립트가 실행을 중단합니다.

```dotenv
POSTGRES_DB=safemaint
POSTGRES_USER=safemaint
POSTGRES_PASSWORD=change-this-local-password
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://safemaint:change-this-local-password@db:5432/safemaint
```

설정이 끝나면 로컬에서 실행 중인 `npm run dev`와 `uvicorn`을 먼저 `Ctrl+C`로 종료합니다. 로컬 프로세스가 3000·8000 포트를 사용 중이면 Docker의 `frontend`·`backend` 컨테이너가 `Created` 상태에 머물며 브라우저에는 `Failed to fetch`가 표시됩니다.

이후 다음 명령 하나로 DB, migration, seed, backend, frontend를 모두 Docker에서 실행합니다.

```powershell
.\scripts\setup-dev.ps1
```

PowerShell 실행 정책으로 차단되면 현재 터미널에서만 허용하고 다시 실행합니다.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-dev.ps1
```

스크립트를 사용하지 않을 경우의 동일한 수동 명령은 다음과 같습니다.

```powershell
docker compose `
  --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.dev.yml `
  up -d --build
```

실행 순서는 Compose가 다음과 같이 보장합니다.

```text
db healthy → migrate 종료 코드 0 → seed 종료 코드 0 → backend healthy → frontend
```

- 대시보드: <http://localhost:3000>
- API 문서: <http://localhost:8000/docs>
- 준비 상태: <http://localhost:8000/health/ready>

실행 상태와 로그는 다음 명령으로 확인합니다. `db`와 `backend`는 `healthy`, `frontend`는 `running`이어야 합니다.

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml ps -a
docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml logs --tail 100 backend frontend
```

종료할 때는 DB 볼륨을 유지하는 다음 명령을 사용합니다.

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml down
```

> `down -v`는 PostgreSQL 데이터를 삭제하므로 사용하지 마세요.

전체 DB 상태를 다시 확인할 때는 다음 명령을 사용합니다.

```powershell
.\scripts\verify-db.ps1
```

`setup-dev.ps1`과 `verify-db.ps1`은 Docker 엔진, 환경변수, PostgreSQL, pgvector, Alembic, seed, 주요 테이블과 backend 상태를 확인합니다. 실패하면 성공 메시지를 출력하지 않고 관련 Compose 상태와 최근 로그를 보여줍니다.

### 포트 충돌

PC에 설치된 다른 PostgreSQL이 이미 5432 포트를 사용한다면 `.env`만 변경합니다.

```dotenv
POSTGRES_PORT=5433
```

이 경우 DBeaver도 5433 포트로 연결합니다. `DATABASE_URL`의 `db:5432`는 컨테이너 내부 주소이므로 변경하지 않습니다.

### DBeaver 연결

| 항목 | 값 |
|---|---|
| Database Type | PostgreSQL |
| Host | `127.0.0.1` |
| Port | `.env`의 `POSTGRES_PORT`, 기본 `5432` |
| Database | `safemaint` |
| Username | `safemaint` |
| Password | 각자 `.env`의 `POSTGRES_PASSWORD` |

연결 후 다음 경로에서 테이블을 확인합니다.

```text
safemaint → Schemas → public → Tables
```

DBeaver SQL Editor에서 다음 쿼리를 실행하면 연결·확장·마이그레이션·seed를 직접 확인할 수 있습니다.

```sql
SELECT current_database(), current_user, version();

SELECT extname, extversion
FROM pg_extension
WHERE extname = 'vector';

SELECT version_num
FROM alembic_version;

SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

SELECT category, COUNT(*)
FROM reference_codes
GROUP BY category
ORDER BY category;

SELECT code, name, is_active
FROM roles
ORDER BY code;

SELECT employee_number, name, department, status
FROM users
ORDER BY employee_number;
```

최초 seed 후 `roles`에는 `admin`, `safety_manager`, `worker` 3건이 보이고 `users`는 비어 있는 것이 정상입니다. 실제 사용자와 비밀번호는 seed나 Git으로 배포하지 않습니다.

### 팀원별 DB와 Git 공유 범위

Git으로 공유되는 것은 Docker Compose 설정, SQLAlchemy 모델, Alembic 마이그레이션, seed 코드와 `.env.example`입니다. 다음 항목은 Git으로 공유하지 않습니다.

- `.env`와 실제 비밀번호
- Docker DB 볼륨과 팀원이 직접 입력한 데이터
- DB dump·backup 파일
- 실제 사업장 문서와 제조사 비공개 자료
- 개인정보와 고객 데이터

각 팀원은 동일한 주요 테이블 16개, `reference_codes` seed 25건과 역할 seed 3건을 갖지만, `users`나 `assessments` 등에 직접 입력한 데이터는 다른 팀원에게 자동으로 전달되지 않습니다. 각자 `setup-dev.ps1` 실행 후 DBeaver로 자신의 로컬 DB에 접속해 확인합니다.

```text
팀원 A → 팀원 A PC의 Docker DB
팀원 B → 팀원 B PC의 Docker DB
팀원 C → 팀원 C PC의 Docker DB
```

팀 전체가 동일한 실제 데이터를 확인해야 할 때는 로컬 DB가 아니라 접근권한과 백업 정책이 마련된 별도 staging DB를 사용해야 합니다.

> `docker compose down -v`와 `docker volume rm`은 DB 데이터를 삭제할 수 있으므로 사용하지 마세요. 설정·검증 스크립트도 이 명령을 실행하지 않습니다.

## 사원번호 기반 회원·권한 DB

회원가입·로그인 화면은 기존 회원·권한 DB와 연결되어 있습니다. 로컬 계정 비밀번호는 Argon2id로 해시한 뒤 저장하고, 회원가입 시 기본 `worker` 역할을 자동 부여합니다. 로그인 실패가 5회 누적되면 15분간 계정을 잠그며 가입·로그인·잠금 이벤트는 `audit_events`에 기록합니다.

- `users.id`: 다른 테이블이 참조하는 내부 UUID 기본키
- `users.employee_number`: 실제 로그인 ID로 사용할 최대 30자의 문자열. 앞자리 `0`을 보존하며 입력 시 공백 제거·대문자 정규화 후 저장합니다.
- `users.auth_provider`: `local`, `ldap`, `oidc` 중 하나
- `users.status`: `active`, `locked`, `retired` 중 하나. 이력 보존을 위해 퇴사자는 삭제하지 않고 `retired`로 전환합니다.
- `users.password_hash`: 로컬 인증 사용자에게만 필수입니다. 평문 비밀번호는 DB, 로그, API 응답과 Git에 저장하지 않습니다.
- `roles`: `worker`, `safety_manager`, `admin`을 멱등 seed로 관리합니다.
- `user_roles`: 사용자와 역할의 N:M 관계이며 같은 역할을 중복 부여할 수 없습니다.
- `user_sites`: 사용자와 사업장의 N:M 접근 관계이며 사용자당 주 사업장은 최대 하나입니다.

이메일은 값이 있을 때 대소문자를 무시하고 유일해야 합니다. 사용자·역할·사업장 권한은 물리 삭제보다 비활성화와 이력 보존을 우선하며, 향후 권한 부여·회수 API는 `audit_events`에 행위자와 변경 내용을 남겨야 합니다.

`assessments.created_by_user_id`, `assessments.reviewed_by_user_id`, `checklist_items.completed_by_user_id`, `audit_events.actor_user_id`는 새 사용자 UUID를 선택적으로 참조합니다. 기존 `reviewed_by`, `completed_by`, `actor_id` 문자열은 과거 데이터 호환을 위해 이번 단계에서 유지하고 애플리케이션 전환이 끝난 뒤 별도 마이그레이션으로 정리합니다.

현재 구조는 한 고객사 내부 설치를 기준으로 합니다. 여러 고객사를 한 DB에 함께 저장하는 SaaS 구조로 전환할 때는 `organizations`와 각 업무 테이블의 `organization_id`를 별도 마이그레이션으로 추가해야 합니다.

현재 로그인 성공 정보는 프런트엔드 화면 전환을 위해 브라우저 `localStorage`에만 보관하며, 아직 서버가 발급한 인증 토큰은 아닙니다. 다음 단계에서는 JWT access/refresh 토큰과 회수 정책, 보호 API의 역할·사업장 범위 인가, 관리자 전용 계정 관리와 권한 변경 감사 이벤트를 구현해야 합니다. DBeaver에서 임의로 평문 비밀번호나 기본 관리자 계정을 넣지 마세요.

## 선택 실행: DB는 Docker, 백엔드 앱은 로컬

필수 환경은 Python 3.13 이상, Node.js 22.13 이상입니다.

백엔드 코드를 `--reload`로 개발할 때만 사용하는 방식입니다. 전체 Docker 방식과 동시에 실행하면 8000 포트가 충돌합니다. 먼저 전체 Compose를 종료한 후 DB만 시작하세요. `.env`의 DB 비밀번호와 PowerShell의 `DATABASE_URL` 비밀번호를 일치시켜야 합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements-dev.txt

docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml up -d db
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

## Supertonic 3 한국어 음성 안내

메인 화면의 `음성 안내` 버튼은 backend의 `POST /api/v1/speech/synthesize`를 호출해 WAV 음성을 재생합니다. 음성 엔진은 CPU용 Supertonic 3이며 `ko` 한국어 모드로 실행됩니다. 첫 음성 생성 시 약 400MB 모델 파일을 내려받기 때문에 인터넷 연결이 필요하고 시간이 걸릴 수 있으며, 이후에는 `safemaint_model_cache` Docker 볼륨을 재사용합니다.

기본 설정은 `.env`에서 변경할 수 있습니다.

```dotenv
TTS_VOICE=F1
TTS_LANGUAGE=ko
TTS_STEPS=8
MODEL_CACHE_VOLUME_NAME=safemaint_model_cache
```

DB 볼륨과 마찬가지로 모델 캐시를 유지하려면 `docker compose down -v`를 사용하지 마세요. `TTS_VOICE`는 `M1`~`M5`, `F1`~`F5` 중 선택할 수 있고, `TTS_STEPS`는 5~12 범위에서 높일수록 품질과 생성 시간이 증가합니다.

## 선택 실행: 프론트엔드 앱은 로컬

프론트엔드 코드를 Hot Reload로 개발할 때만 사용합니다. Docker의 `frontend`가 실행 중이면 3000 포트가 충돌하므로 먼저 해당 컨테이너를 중지합니다. 백엔드는 Docker 또는 로컬 방식 중 하나로 8000 포트에서 실행되어 있어야 합니다.

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml stop frontend
```

그다음 새 터미널에서 실행합니다.

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

- 대시보드: <http://localhost:3000>
- 회원가입: <http://localhost:3000/signup>

### `Failed to fetch` 점검

회원가입·로그인에서 `Failed to fetch`가 나오면 백엔드가 실행되지 않았거나 8000 포트가 다른 로컬 프로세스와 충돌한 상태입니다.

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health/ready
docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml ps -a
```

정상 응답은 HTTP 200과 `"database":"connected"`입니다. `backend`가 `Created` 상태라면 로컬 `uvicorn`을 `Ctrl+C`로 종료하고 `setup-dev.ps1`을 다시 실행하세요.

## DB 마이그레이션과 업데이트

스키마는 `Base.metadata.create_all()`이 아니라 Alembic으로만 변경합니다.

```powershell
python -m alembic -c backend\alembic.ini revision --autogenerate -m "변경 내용"
python -m alembic -c backend\alembic.ini upgrade head
```

프로그램 업데이트 시 새 코드를 받은 뒤 같은 실행 명령을 사용하면 기존 named volume은 유지되고 새로운 마이그레이션과 seed만 적용됩니다.

```powershell
git pull origin dev
.\scripts\setup-dev.ps1
.\scripts\verify-db.ps1
```

기존 named volume은 유지되고 새로운 마이그레이션과 seed만 다시 적용됩니다. 운영용 설치·업데이트·백업·복구 자동화와 외부 DB 전용 Compose 프로필은 별도 배포 단계에서 추가합니다. 그 전에는 고객사 실데이터를 넣지 마세요.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/auth/register` | 로컬 계정 생성, Argon2id 해시 저장, 기본 `worker` 역할 부여 |
| `POST` | `/api/v1/auth/login` | 사원번호·비밀번호 확인, 실패 횟수 및 15분 잠금 처리 |
| `POST` | `/api/v1/speech/synthesize` | Supertonic 기반 한국어 안전 안내 WAV 생성 |
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

### Alembic과 seed 협업 규칙

- 이미 공유되거나 적용된 Alembic 마이그레이션 파일은 수정하지 않습니다.
- 스키마 변경마다 `alembic revision --autogenerate`로 새로운 revision을 만듭니다.
- 생성한 마이그레이션 파일을 관련 코드와 함께 Git에 커밋합니다.
- pull 이후 `setup-dev.ps1`을 실행해 `alembic upgrade head`가 자동 적용되는지 확인합니다.
- 여러 migration head가 의심되면 `python -m alembic -c backend\alembic.ini heads`로 확인합니다.
- seed는 여러 번 실행해도 중복되지 않는 멱등 구조를 유지합니다.
- 운영 DB dump, 실제 고객 데이터와 사업장 문서는 Git에 커밋하지 않습니다.

## 프론트엔드 초기 구상 화면

현재 프론트엔드에는 다음 화면 흐름이 통합되어 있습니다.

- PostgreSQL 기반 회원가입·로그인과 브라우저 화면 세션
- 메뉴: 음량, 글자 크기, 결과 기록, 대화 초기화, 로그아웃
- 아바타·검색 결과·영상 표시 영역
- 사용자 보유 PDF 매뉴얼 선택 영역
- 채팅 UI와 결과 기록 저장
- 기존 FastAPI `/api/v1/assessments/preview` 연동 위험성평가 미리보기

계정은 FastAPI와 PostgreSQL에 저장됩니다. 현재 브라우저 `localStorage`에는 화면 세션·설정·결과 기록만 저장되며, 서버가 발급하는 JWT와 보호 API는 후속 단계에서 구현합니다.
