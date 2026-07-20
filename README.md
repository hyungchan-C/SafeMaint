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

채팅 화면에서 실제 BGE-M3 검색과 문서 출처를 사용하려면 공통 Compose의
`rag`와 `worker`를 함께 실행합니다.

검색할 문서 종류는 코드에 고정하지 않고 `.env`의 쉼표 구분 설정으로 선택합니다.

```dotenv
RAG_SOURCE_TYPES=incident,manual
```

값을 비우면 `private` 문서를 제외한 모든 source type을 검색합니다. 실제 고객
매뉴얼은 업로드·권한 검증·전처리가 완료된 문서만 검색 범위에 포함해야 합니다.

```powershell
docker compose `
  --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.dev.yml `
  up -d --build
```

RAG API는 Docker 내부의 `http://rag:8010`에서 backend만 접근하며 호스트에는
포트를 공개하지 않습니다. `docker-compose.rag.yml`은 예전 실행 명령과의 호환을
위해 timeout 설정만 남긴 선택 오버레이입니다.

RAG 서비스가 실행되지 않거나 검색 DB가 준비되지 않은 경우에도 채팅 API는 작업
키워드에 맞는 공통 안전수칙을 반환하며, 화면에 근거 검색이 연결되지 않았다는
경고를 표시합니다. 화면의 매뉴얼 선택은 업로드·처리·승인이 끝난 문서와 버전의
UUID를 전달할 때만 검색 범위를 제한합니다.

`ALLOW_EXTERNAL_LLM=true`이고 provider 인증 정보가 설정되어 있으면 채팅은
**BGE-M3·pgvector 검색 → 공개 검색 근거와 작업정보를 OpenAI 호환 모델에 전달
→ 답변과 검색 출처를 함께 표시**하는 순서로 동작합니다. 외부 호출이 꺼져 있거나
요청이 실패하면 검색 서비스의 기본 안전 안내로
자동 전환하므로 RAG 근거는 유지됩니다. 현재 OpenAI 연동은 Qwen3 적용 전 실험용이며,
모델 호출부는 `backend/app/services/ai.py`에 분리되어 있습니다.

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

실험용 OpenAI 답변 생성을 사용하려면 API 키도 입력합니다. 사용하지 않으면 BGE-M3
검색과 규칙 기반 안전 안내만 동작합니다. `.env`는 Git에서 제외되므로 실제 키를
`.env.example`이나 소스 코드에 넣지 마세요.

```dotenv
OPENAI_API_KEY=sk-여기에_본인의_API_키
OPENAI_MODEL=gpt-4o-mini
LLM_BASE_URL=
LLM_ANALYZER_MODEL=gpt-4o-mini
LLM_ANSWER_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=30
OPENAI_MAX_OUTPUT_TOKENS=1200
ALLOW_EXTERNAL_LLM=true
```

외부 LLM은 기본적으로 꺼져 있습니다. 명시적으로 켜면 질문과 검색 근거 일부가 외부
API로 전송될 수 있으므로 공개 자료 실험에만 사용하세요. 회사 문서 근거는 설정값과
관계없이 외부 모델로 전송되지 않습니다. 실제 고객정보나 개인정보를 입력하기 전
조직의 데이터 처리 기준을 확인해야 합니다.

설정을 변경한 뒤에는 `verify-db.ps1`이 아니라 `setup-dev.ps1` 또는 아래 Compose 명령으로 backend를 다시 빌드해야 적용됩니다.

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml build backend
docker compose --env-file .env -f docker-compose.yml -f docker-compose.dev.yml up -d --no-build backend
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
  build backend rag frontend

docker compose `
  --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.dev.yml `
  up -d --no-build
```

실행 순서는 Compose가 다음과 같이 보장합니다.

```text
db healthy → migrate 종료 코드 0 → seed 종료 코드 0 → backend/RAG/worker → frontend
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

Windows가 3000 포트를 사용 중이거나 예약한 경우에는 `.env`의 호스트 포트만 바꿉니다.

```dotenv
FRONTEND_PORT=3030
```

이 경우 대시보드 주소는 `http://localhost:3030`입니다. 컨테이너 내부 포트는 계속 3000이므로 다른 설정은 변경하지 않습니다.

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

`POST /api/v1/auth/login`은 이제 `auth_sessions` 테이블에 저장되는 서버 세션 토큰을 발급합니다(원문은 클라이언트에만 반환되고 DB에는 SHA-256 해시만 저장, 기본 만료 `SESSION_EXPIRE_MINUTES`분). 보호가 필요한 라우트는 `app.api.deps.get_current_user`를 `Depends`로 붙여 `Authorization: Bearer <token>` 헤더를 검증하며, `POST /api/v1/auth/logout`으로 즉시 무효화(회수)할 수 있습니다. 아직 역할·사업장 범위 인가, 관리자 전용 계정 관리와 권한 변경 감사 이벤트는 구현되지 않았습니다. DBeaver에서 임의로 평문 비밀번호나 기본 관리자 계정을 넣지 마세요.

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

## 오프라인 카탈로그·현장 사진 분석

`docker-compose.vision.yml`은 인터넷이 없는 현장을 위한 선택 서비스입니다.
PaddleOCR-VL 0.9B가 카탈로그의 글자·표·레이아웃을 추출하고,
DINOv2-small이 PDF에서 추출한 제품 이미지와 현장 사진의 외형 후보를 검색하며,
Qwen3-VL-2B-Instruct가 이미지 구조화와 후보 의미 검토를 수행합니다. 모델은 모두 로컬에
저장되며 OpenAI API 키를 사용하지 않습니다.

사진 첨부는 2단계로 동작합니다. 먼저 `analysis_mode=fast`가 DINOv2 임베딩의 일괄
행렬 검색으로 후보를 즉시 표시하고, 이어서 `analysis_mode=deep`가 Qwen 검토를
수행해 같은 화면의 후보와 채팅 문맥을 정밀 결과로 갱신합니다. 현장 사진에서는 느린
문서용 PaddleOCR-VL을 생략하며, Qwen 입력은 최대 1,048,576픽셀로 제한합니다. 빠른 후보에는 모델·규격
확정 의미가 없으며, 정밀 분석이 끝나기 전에도 외형 후보를 먼저 확인할 수 있습니다.

RTX 2060 6GB 기준으로 PaddleOCR-VL은 CPU, Qwen3-VL은 GPU 4비트로 실행합니다.
두 모델이 좁은 VRAM을 동시에 점유하지 않게 분리한 구성입니다. 처음 한 번은
인터넷이 되는 환경에서 이미지와 모델을 내려받아야 합니다.

```powershell
docker compose `
  --env-file .env `
  -f docker-compose.yml `
  -f docker-compose.dev.yml `
  -f docker-compose.vision.yml `
  up -d --build vision backend frontend
```

첫 실행에서는 모델 다운로드, Triton 커널 컴파일과 초기화 때문에 5~15분 정도 걸릴
수 있습니다. RTX 2060 6GB에서는 Qwen 분석 중 GPU 메모리를 거의 모두 사용하므로
다른 GPU 프로그램을 함께 실행하지 않는 것을 권장합니다. 이후 요청은 프로세스에
로드된 모델을 재사용합니다.

비전 컨테이너는 호스트 포트를 열지 않으며 Docker 내부 네트워크에서 백엔드만
접근합니다. 브라우저와 외부 도구는 로그인 후 발급받은 Bearer 토큰으로
`http://localhost:8000/api/v1/vision/...` 백엔드 API만 호출해야 합니다.

모델 다운로드가 끝난 뒤 인터넷 차단 환경에서는 `.env`를 다음처럼 바꾸고 서비스를
재시작합니다.

```dotenv
HF_HUB_OFFLINE=1
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=true
```

모델은 `safemaint_vision_models`, 카탈로그 이미지 인덱스는
`safemaint_catalog_index` Docker 볼륨에 보관됩니다. 오프라인 배포 전에 두 볼륨을
현장 장비로 별도 복제해야 하며, `docker compose down -v`를
실행하면 모델 캐시가 삭제되므로 사용하지 마세요.

OCR만 필요한 저사양 현장 장비에서는 `.env`의 `VISION_ENABLE_QWEN=false`로 두면
PaddleOCR-VL만 CPU에서 실행됩니다. 이미지 의미 분석이 필요하면 기본값 `true`를
사용합니다.

매뉴얼 첨부 UI는 문서 업로드 후 반환된 `document_id`로
`POST /api/v1/vision/catalog/index`를 호출하고, 사진 첨부 UI는 접근 가능한
`document_ids`와 함께 `POST /api/v1/vision/catalog/match`를 호출합니다. 원본 PDF를
인덱싱 API에 다시 보내거나 내부 `catalog_id`를 브라우저에 저장하지 않습니다. 백엔드는
문서 소유자·역할·사업장 권한을 확인한 뒤 PDF의 제품 이미지를 추출해 로컬
임베딩으로 후보를 좁히고 Qwen3-VL이 지도·인증서·로고·다른 부품을 다시 걸러냅니다.
후보는 같은 모델이나 규격의 확정 근거가 아니며, 신뢰 임계값을 넘지 못하면 표시하지
않습니다. 분석된
JSON은 현재 브라우저 탭의 메모리에서만 채팅의 로컬 이미지 분석 문맥으로 사용하며
OCR·후보·내부 인덱스 ID는 `localStorage`에 저장하지 않습니다. 이미지 분석 문맥이
포함된 요청은 외부 LLM 답변 생성을 건너뛰어 현장 이미지 정보가 외부로 전송되지
않습니다. 향후 사내 Qwen 계열 모델로 교체해도 이 비전 서비스는 그대로 사용할 수
있습니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/auth/register` | 로컬 계정 생성, Argon2id 해시 저장, 기본 `worker` 역할 부여 |
| `POST` | `/api/v1/auth/login` | 사원번호·비밀번호 확인, 실패 횟수 및 15분 잠금 처리, 세션 토큰 발급 |
| `POST` | `/api/v1/auth/logout` | 현재 세션 토큰 무효화(회수) |
| `POST` | `/api/v1/documents/upload` | PDF 업로드(인증 필요), `document_versions`/`document_processing_jobs` 생성 후 워커가 비동기 처리 |
| `POST` | `/api/v1/documents/{document_id}/versions/{version_id}/approve` | `document.approve` 권한으로 검토 완료 버전을 활성화하고 기존 버전을 superseded 처리 |
| `POST` | `/api/v1/vision/catalog/index` | `document.upload` 권한과 소유권을 확인해 저장된 PDF의 로컬 이미지 인덱스 생성 |
| `POST` | `/api/v1/vision/catalog/match` | `document.read` 권한 범위의 문서만 현장 사진과 로컬 비교 |
| `GET` | `/api/v1/vision/catalog/image/{document_id}/{page}/{image_index}` | 문서 접근 권한 확인 후 후보 이미지 반환(`private, no-store`) |
| `POST` | `/api/v1/chat` | 익명은 공용 문서만, 로그인 사용자는 서버가 계산한 역할·사업장 범위로 RAG 검색 |
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

## 고객사별 온프레미스 배포·보안 구조

SafeMaint는 고객사별로 완전히 분리해 설치하는 single-tenant 온프레미스 시스템입니다. 각 고객사는 동일한 이미지와 스키마를 사용하지만 PostgreSQL, 원본 PDF, 임베딩, 계정, 감사기록은 각 고객사 서버의 Docker volume에만 저장됩니다. 중앙 관리 환경에는 고객사 문서·질문·응답·임베딩을 수집하거나 전송하는 코드 경로가 없습니다.

실행 서비스는 `frontend`, `backend`, `rag`, `worker`, `db`입니다. `migrate`와 `seed`는 성공 후 종료되는 일회성 서비스입니다. 운영 Compose에서는 DB와 RAG 포트를 호스트에 공개하지 않습니다. 개발 오버레이도 DB만 `127.0.0.1`에 공개하고 RAG는 계속 Docker 내부에서만 접근합니다.

영구 데이터:

- `safemaint_postgres_data`: PostgreSQL + pgvector
- `safemaint_documents`: 고객사 원본 PDF
- `safemaint_packages`: 검증을 통과한 공용 RAG 패키지
- `safemaint_model_cache`: 로컬 임베딩·TTS 모델 캐시

백업 시에는 DB와 `safemaint_documents`를 같은 복구 시점으로 함께 보호해야 합니다. 스크립트는 `docker compose down -v`나 `docker volume rm`을 실행하지 않습니다.

### 인증·문서 권한

DB의 `permissions`, `role_permissions`, `user_roles`, `user_sites`를 기준으로 백엔드가 권한을 계산합니다. 로그인 세션의 Bearer 토큰만 신뢰하며 클라이언트가 보낸 역할명, `site_id`, `allow_company` 값으로 권한을 올리지 않습니다.

| 역할 | 주요 문서 권한 |
|---|---|
| `worker` | 승인된 접근 가능 문서 조회·검색 |
| `safety_manager` | 조회, 업로드, 메타데이터 수정, 교체 버전 업로드 |
| `document_manager` | 위 권한 + 승인, 삭제, 복구, 감사 조회 |
| `admin` | 모든 권한, 역할 관리, 공용 패키지 가져오기·롤백 |

권한 코드는 `document.read`, `document.upload`, `document.update`, `document.replace`, `document.approve`, `document.delete`, `document.restore`, `role.manage`, `audit.read`, `public_package.import`입니다. 익명 채팅은 항상 public-only입니다. `document.read`가 있는 일반 사용자는 활성 `UserSite.site_id`만 회사 문서 범위로 받고, `document_manager`와 `admin`은 모든 사업장을 조회할 수 있습니다. 별도 정책이 없는 `private` 문서는 어느 경우에도 허용하지 않습니다. `selected_document_ids`와 `selected_document_version_ids`는 이 범위를 더 좁히는 교집합 필터이며 권한 상승 수단이 아닙니다.

### 고객사 PDF 처리 흐름

인증된 `document.upload` 사용자는 `/api/v1/documents/upload`로 회사 문서를 등록합니다. 일반 업로드 API는 회사 범위 문서 유형과 `restricted`/`private` 접근 수준만 허용하므로 `public_guide` 또는 `public` 문서로 위장 등록할 수 없습니다. 업로드는 원본 파일, SHA-256, 새 `DocumentVersion`, `DocumentProcessingJob`을 만들며 같은 모델의 재업로드는 새 버전 번호를 부여합니다.

```text
pending → processing → review_required → active
                    └→ failed 또는 ocr_required
```

worker가 고객사 서버 안에서 PyMuPDF로 텍스트를 추출하고 BGE-M3 1024차원 임베딩을 생성합니다. 텍스트가 없는 스캔 PDF는 내용을 꾸며내지 않고 `ocr_required`로 표시합니다. 처리된 문서는 `review_required`에서 대기하며 `document.approve` 권한 사용자가 승인 API를 호출해야 `active/current_version`이 됩니다. 승인은 row lock과 단일 트랜잭션으로 실행되고 이전 활성 버전을 `superseded`로 바꾸며 감사 이벤트를 남깁니다. 검색은 승인된 현재 버전만 대상으로 합니다.

문서 유형은 `public_incident`, `public_law`, `public_guide`, `public_media`, `company_policy`, `equipment_manual`, `component_manual`입니다. 향후 고객사 업로드 API는 회사 문서 유형만 허용하고 공용 유형은 서명된 패키지로만 설치해야 합니다.

### 서명된 공용 RAG 패키지

패키지는 안전한 ZIP 안에 canonical JSON인 `manifest.json`, `documents.jsonl`, `chunks.jsonl`, `signature.ed25519`만 포함합니다. pickle은 사용하지 않습니다. manifest에는 패키지·스키마 버전, 모델, 차원, 출처 유형, 문서/청크 수, 파일별 SHA-256, 이전 버전 정보가 들어갑니다.

`safemaint_api_data`에는 국내재해·사고사망 및 안전보건법령 스마트검색 수집기와 공용 패키지 생성기가 있습니다. 각 하위 프로젝트의 `output/`은 로컬 생성 데이터이므로 Git에 포함되지 않습니다. 공용 패키지 생성기는 별도 `rag_documents`/`rag_chunks` 테이블이나 직접 적재 SQL을 만들지 않고, 기존 Alembic `documents`/`document_chunks` 형식과 아래 Ed25519 검증 CLI를 사용합니다. 자세한 입력 규격과 빌드 명령은 `safemaint_api_data/safemaint_public_rag_package/README.md`를 참고하세요.

중앙 생성 환경에서 Ed25519 키를 별도 비밀 저장소에 보관하고 다음 CLI를 사용합니다.

```powershell
docker compose run --rm backend python -m app.commands.public_rag_package create `
  --version "2026.07.16" `
  --private-key "/run/secrets/public-package-private.pem" `
  --output "/data/packages/safemaint-public-2026.07.16.zip"
```

고객사에서는 공개키만 배포하고 검증 후 가져옵니다.

```powershell
docker compose run --rm backend python -m app.commands.public_rag_package verify `
  "/data/packages/safemaint-public-2026.07.16.zip" `
  --public-key "/run/secrets/public-package-public.pem"

docker compose run --rm backend python -m app.commands.public_rag_package import `
  "/data/packages/safemaint-public-2026.07.16.zip" `
  --public-key "/run/secrets/public-package-public.pem"

docker compose run --rm backend python -m app.commands.public_rag_package status
docker compose run --rm backend python -m app.commands.public_rag_package rollback --version "이전버전"
```

서명, 체크섬, 파일 목록, 공용 유형, 스키마, 모델, 차원, 청크 참조를 모두 먼저 검증합니다. 가져오기는 PostgreSQL advisory lock과 단일 트랜잭션을 사용하며 완료 전에 기존 활성 패키지를 비활성화하지 않습니다. 회사 문서 유형은 생성·검증 단계에서 패키지에 포함될 수 없습니다.

### 외부 전송 차단

기본값은 다음과 같으며 `.env.example`에도 동일하게 선언되어 있습니다.

```dotenv
ALLOW_EXTERNAL_LLM=false
```

이 값이 `false`이면 외부 모델 호출 자체가 발생하지 않고 로컬 RAG와 안전 템플릿만 사용합니다. 외부 모델을 명시적으로 켜더라도 회사 범위 근거가 하나라도 포함되면 코드 수준에서 외부 전송을 차단합니다. 예전 직접 OpenAI 채팅 경로는 공개 API 라우터에서 제거했습니다. TTS 기능은 유지됩니다.

### 최초 설치와 업데이트

개발 PC 최초 실행:

```powershell
Copy-Item .env.example .env
notepad .env
.\scripts\setup-dev.ps1
.\scripts\verify-db.ps1
```

업데이트 시 기존 volume을 삭제하지 말고 다음 명령을 사용합니다.

```powershell
git pull
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
.\scripts\verify-db.ps1
```

운영 설치에서는 `docker-compose.dev.yml`을 사용하지 않고 정확한 HTTPS origin, 강한 DB 암호, 패키지 공개키를 설정합니다. 팀원 인증 구현이 합쳐지면 토큰 저장·만료·폐기와 HTTPS 전용 쿠키 또는 Authorization 헤더 정책을 함께 확정해야 합니다. 방화벽과 reverse proxy에서는 frontend/backend만 필요한 범위로 노출합니다.

### 현재 구현 범위와 남은 항목

구현됨: DB 기반 RBAC와 세션 토큰, 인증된 PDF 업로드 API(F-01), 문서 유형·버전·처리 상태, 로컬 텍스트 추출·BGE-M3 임베딩 worker, 트랜잭션 기반 승인 API, 승인된 현재 버전 검색, 인증 사용자의 역할·사업장 검색 범위, 출처의 문서 유형·파일명·페이지·섹션·버전·점수, 서명 패키지 CLI, 감사 데이터, 외부 LLM 기본 차단, TTS.

프런트엔드의 문서 업로드·승인 관리 화면은 이번 E2E 범위에 포함하지 않았습니다. HTTP API와 백엔드 보안 흐름까지 자동 검증합니다.

별도 운영 작업 필요: 고객사 HTTPS 인증서/reverse proxy, Ed25519 키 수명주기와 오프라인 전달 절차, 실제 백업·PITR 자동화, 스캔 PDF용 검증된 로컬 OCR 엔진, 악성 PDF 안티바이러스/CDR, 로그 보존·모니터링 정책. 이 항목들은 동작하는 것처럼 화면에 표시하지 않습니다.

## 하이브리드 RAG와 임시 GPT-4o-mini 설정

현재 질의 흐름은 `상황 분석 → 키워드·BGE-M3 후보 검색 → 주제 불일치 제거 → rerank → 근거 답변` 순서입니다. 상황 분석기와 답변 생성기는 같은 OpenAI 호환 provider 인터페이스를 사용합니다. 팀의 Qwen3 서버가 준비되기 전에는 다음처럼 GPT-4o-mini를 사용합니다.

```dotenv
OPENAI_API_KEY=각자_발급한_키
ALLOW_EXTERNAL_LLM=true
LLM_BASE_URL=
LLM_ANALYZER_MODEL=gpt-4o-mini
LLM_ANSWER_MODEL=gpt-4o-mini
LLM_ANALYZER_MAX_OUTPUT_TOKENS=500
OPENAI_MAX_OUTPUT_TOKENS=1200
```

Qwen3가 OpenAI 호환 API로 준비되면 애플리케이션 코드를 수정하지 않고 `LLM_BASE_URL`, `LLM_ANALYZER_MODEL`, `LLM_ANSWER_MODEL`만 변경합니다. 모델 분석 JSON이 잘못되거나 시간 초과가 발생하면 입력값 기반 검색어로 대체합니다. 검색 근거가 없으면 모델이 답을 추측하지 않고 “근거 없음” 응답을 반환합니다. 회사 범위 문서가 결과에 포함된 경우에는 외부 모델 답변 생성을 항상 차단합니다.

익명 요청의 검색 범위는 항상 공개 문서뿐입니다. 로그인 요청은 세션 사용자와 DB 권한·사업장 배정을 기준으로 서버가 범위를 생성합니다. 화면에서 선택한 매뉴얼은 파일명이 아니라 아래 UUID 필드로 전달해야 합니다.

```json
{
  "context": {
    "selected_document_ids": ["document-uuid"],
    "selected_document_version_ids": ["document-version-uuid"]
  }
}
```

기존 `registered_manuals` 값이 UUID이면 단계적으로 호환되지만, 파일명 문자열은 검색 권한이나 범위로 사용하지 않습니다. 클라이언트가 보낸 `allow_company`, 역할명 또는 사업장 값은 신뢰하지 않습니다.

문서 worker는 팀의 `ai/preprocessing/pdf_pipeline.py`를 공통 처리 경로로 사용합니다. 텍스트 PDF는 페이지·섹션·제조사·제품군·모델 메타데이터와 결정적 청크 ID/해시를 저장합니다. 이미지 전용 PDF는 OCR이 준비되지 않은 경우 `ocr_required`, 내용이 없는 PDF는 `failed`로 구분합니다. 중단된 `processing` 작업은 제한 횟수 내 재시도하고 최종 실패 사유와 감사 이벤트를 남깁니다.

```dotenv
DOCUMENT_WORKER_MAX_ATTEMPTS=3
DOCUMENT_WORKER_RETRY_DELAY_SECONDS=10
DOCUMENT_WORKER_STALE_AFTER_SECONDS=300
RAG_CANDIDATE_K=30
RAG_MAX_CHUNKS_PER_DOCUMENT=2
RAG_MIN_KEYWORD_SCORE=0.08
```

개발 검증은 운영 이미지와 분리된 Docker test stage에서 실행할 수 있습니다. DB 통합 테스트는 이름이 `_test`로 끝나는 별도 DB에서만 실행해야 합니다.

```powershell
docker build --target test -t safemaint-backend:test-suite .\backend
docker run --rm safemaint-backend:test-suite

docker build --target test -t safemaint-rag:test-suite -f .\ai\Dockerfile.rag .
docker run --rm safemaint-rag:test-suite
```

## 가짜 PDF RAG·LLM E2E 검증

실제 회사 문서를 사용하지 않고 PyMuPDF가 실행 중 생성하는 ASCII 테스트 PDF로 다음 흐름을 검증합니다.

```text
PDF 업로드 → Worker 전처리·청킹 → BGE-M3 임베딩 → pgvector 저장
→ 문서 승인 → 하이브리드 검색 → 근거 답변과 파일명·페이지·섹션 출처
```

PowerShell 실행 정책이 허용된 터미널에서는 프로젝트 루트에서 다음 한 줄을 실행합니다.

```powershell
.\scripts\run-pdf-rag-e2e.ps1
```

스크립트 실행이 정책으로 차단되면 시스템 설정을 바꾸지 않고 이번 프로세스에만 우회 정책을 적용할 수 있습니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-pdf-rag-e2e.ps1
```

스크립트는 매 실행마다 이름이 `_test`로 끝나는 DB와 고유한 테스트 전용 DB·문서 volume을 사용하고 비밀번호를 메모리에서 생성합니다. 개발·운영 DB와 문서 volume은 사용하거나 삭제하지 않으며 테스트 종료 시 서비스만 내립니다. 모델 cache만 기존 `safemaint_model_cache`를 재사용할 수 있습니다. 실패 시 DB, migration, seed, backend, RAG, worker 로그를 출력합니다.

기본 실행은 실제 BGE-M3까지 검증하고 외부 LLM 호출은 건너뜁니다. 공개 가짜 PDF만 GPT-4o-mini에 보내는 선택 테스트는 키를 소스나 로그에 남기지 않고 다음처럼 명시적으로 실행합니다.

```powershell
$env:OPENAI_API_KEY = "발급받은_테스트용_키"
.\scripts\run-pdf-rag-e2e.ps1 -RunExternalLlm
Remove-Item Env:OPENAI_API_KEY
```

키가 없으면 외부 LLM 항목만 `SKIP`되고 업로드, 권한, Worker, 임베딩, 승인, 검색, 회사 문서 외부 전송 차단 테스트는 계속 실행됩니다. 회사 범위 근거가 검색되면 외부 provider를 호출하지 않고 로컬 템플릿으로 답하며 출처는 그대로 유지합니다. 세부 검증 결과는 [PDF RAG·LLM E2E 보고서](reports/pdf_rag_llm_e2e_report.md)에 기록합니다.
