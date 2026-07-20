# SafeMaint 공용 안전자료 RAG 패키지 생성기

국내재해·사고사망 전처리 결과와 안전보건법령 스마트검색 결과를 통합하고,
BGE-M3 임베딩을 생성한 뒤 SafeMaint 표준 공용 패키지로 내보냅니다.

이 도구는 별도 `rag_documents`/`rag_chunks` 테이블을 만들지 않습니다.
최종 ZIP은 백엔드의 Alembic으로 관리되는 `documents`, `document_chunks`,
`public_rag_packages`에만 적재되며 Ed25519 서명 검증을 반드시 통과해야 합니다.

## Git에 포함되지 않는 파일

다음 입력·출력은 모두 생성 데이터이므로 저장소에 커밋하지 않습니다.

- `safemaint_accident_preprocessing/output/`
- `safemaint_smart_search/output/`
- `safemaint_public_rag_package/output/`
- BGE-M3 모델 캐시
- Ed25519 개인키·공개키

## 입력 파일

기본 경로는 다음 세 파일입니다.

```text
safemaint_accident_preprocessing/output/domestic_cases.csv
safemaint_accident_preprocessing/output/fatal_cases.csv
safemaint_smart_search/output/smart_search_documents.csv
```

경로가 다르면 `--domestic`, `--fatal`, `--smart-documents`로 각각 지정합니다.

## 설치와 검증

```powershell
Set-Location safemaint_api_data/safemaint_public_rag_package
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python build_public_rag_package.py validate
```

임베딩 없이 문서·청크 구조만 검증할 수도 있습니다.

```powershell
python build_public_rag_package.py build --skip-embeddings
```

## 서명 패키지 생성

개인키는 Git 밖의 안전한 위치에 보관합니다. 전체 패키지 생성 시 키 경로를
명시해야 하며, 키가 없으면 unsigned ZIP을 만들지 않고 실패합니다.

```powershell
python build_public_rag_package.py build `
  --private-key "C:\secure\safemaint-public-private.pem" `
  --previous-version "0.9.0"
```

`--private-key` 대신 루트 `.env`와 동일한 이름인
`PUBLIC_PACKAGE_PRIVATE_KEY` 환경변수를 사용할 수 있습니다. 모델 또는 입력이
바뀌어 기존 임베딩을 폐기할 때만 `--reset-embeddings`를 사용합니다.

생성되는 ZIP에는 다음 네 파일만 들어갑니다.

```text
manifest.json
documents.jsonl
chunks.jsonl
signature.ed25519
```

문서는 기존 SafeMaint 문서 유형으로 매핑됩니다.

| 수집기 source_type | SafeMaint source_type | document_type_code |
|---|---|---|
| `domestic_case`, `fatal_case` | `incident` | `public_incident` |
| `law`, `notice` | `regulation` | `public_law` |
| `kosha_guide` | `guide` | `public_guide` |
| `media` | `media` | `public_media` |

알 수 없는 유형은 임의로 적재하지 않고 빌드를 실패시킵니다.

## SafeMaint DB에 검증·적재

생성된 ZIP을 고객사 또는 개발 DB에 직접 푸는 스크립트는 제공하지 않습니다.
저장소의 백엔드 명령만 사용합니다.

```powershell
docker compose run --rm backend python -m app.commands.public_rag_package verify `
  "/data/packages/public_safety_rag_package_v1.0.0.zip" `
  --public-key "/run/secrets/public-package-public.pem"

docker compose run --rm backend python -m app.commands.public_rag_package import `
  "/data/packages/public_safety_rag_package_v1.0.0.zip" `
  --public-key "/run/secrets/public-package-public.pem"
```

이 경로는 ZIP 파일명·SHA-256·Ed25519 서명·스키마 버전·임베딩 모델과 차원을
검증한 다음 Alembic 스키마에 트랜잭션으로 적재합니다. 기존 활성 공용 패키지는
삭제하지 않고 `superseded` 상태로 전환되므로 롤백할 수 있습니다.
