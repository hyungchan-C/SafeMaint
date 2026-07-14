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
- PostgreSQL + pgvector 개발 환경

초기 MVP에서는 사진 인식, VLM, GPS·동선 추적, 자체 LLM 파인튜닝, 복잡한 승인 시스템을 제외합니다. 검색 성능이 확보된 뒤 우선순위에 따라 추가합니다.

## 기술 구성

| 영역 | 기술 |
|---|---|
| 프론트엔드 | Next.js, React, TypeScript |
| 백엔드 | FastAPI, Pydantic |
| 데이터베이스 | PostgreSQL, pgvector |
| 목표 검색 구조 | 메타데이터 필터 + BM25 + BGE-M3 + Reranker |
| 목표 생성 구조 | 파운데이션 모델 + 검색 근거 기반 답변 |
| 위험도 | 규칙 기반 위험성평가 엔진 |

자세한 구조는 [docs/architecture.md](docs/architecture.md)를 참고하세요.

## 폴더 구조

```text
SafeMaint/
├─ backend/             FastAPI API와 위험도 규칙
├─ frontend/            안전관리 대시보드
├─ docs/                설계 문서
├─ .env.example         환경변수 예시
└─ docker-compose.yml   프론트·백엔드·DB 개발 환경
```

## 로컬 실행

필수 환경은 Python 3.13 이상, Node.js 22.13 이상입니다.

### 1. 백엔드

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements-dev.txt
python -m uvicorn app.main:app --app-dir backend --reload
```

- 상태 확인: <http://localhost:8000/health>
- API 문서: <http://localhost:8000/docs>

### 2. 프론트엔드

새 터미널에서 실행합니다.

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

- 대시보드: <http://localhost:3000>

### 3. Docker Compose

Docker Desktop이 설치된 환경에서는 전체 구성을 함께 실행할 수 있습니다.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

## 테스트

```powershell
python -m pytest backend\tests
Set-Location frontend
npm.cmd run typecheck
npm.cmd run build
```

## 협업 규칙

- 기본 브랜치: `main`
- 기능 개발: `feature/기능명`
- 버그 수정: `fix/수정명`
- 기능별 Pull Request를 생성하고 검토 후 병합합니다.
- `.env`, 사업장 내부 문서, 제조사 재배포 제한 자료, 개인정보, 모델 파일은 커밋하지 않습니다.
