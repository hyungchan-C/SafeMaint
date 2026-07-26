# SafeMaint Qwen API 전달 안내

Colab에 올리는 프로젝트 ZIP에는 반드시 최신 `ai/qwen_service/`가 들어가야 합니다.

## 현재 운영 방식: 짧은 카드 정리

이제 Qwen 답변에는 빠른 모드와 structured 모드를 나누지 않습니다.
Qwen은 긴 `structured_answer` JSON 전체를 직접 생성하지 않고, 백엔드가 만든 카드 후보를 짧게 선별/요약/표현 정리합니다.

- `QWEN_LOAD_IN_4BIT=true`
- `QWEN_MAX_NEW_TOKENS=256`
- `QWEN_CLASSIFY_MAX_NEW_TOKENS=64`
- `QWEN_SOURCE_EXCERPT_CHARS=180`
- `QWEN_DOCUMENT_SOURCE_LIMIT=2`
- `QWEN_COMPONENT_SOURCE_LIMIT=2`
- `QWEN_MAINTENANCE_SOURCE_LIMIT=5`
- `QWEN_INTENT_CLASSIFY_ENABLED=true`
- `QWEN_ACCIDENT_CLASSIFY_ENABLED=true`

사용하지 않는 값:

- `QWEN_ANSWER_MODE`
- `QWEN_REPAIR_ENABLED`

## API 역할

- `POST /v1/intent`: 질문유형이 애매할 때만 일반 Qwen으로 질문유형을 보정합니다.
- `POST /v1/classify`: 유지보수/안전 질문에서 LoRA Qwen으로 사고유형만 분류합니다.
- `POST /v1/answer`: 백엔드 후보 카드와 짧은 RAG 근거를 받아 화면 카드 문구를 짧게 정리합니다.

백엔드는 Qwen 결과를 그대로 믿지 않고, 근거 ID와 문서 타입을 다시 검증한 뒤 기존 프론트 구조로 조립합니다.

## 백엔드 설정

Colab ngrok URL을 받은 뒤 각자 `.env`에는 다음 값을 맞춥니다.

```env
QWEN_ENABLED=true
QWEN_PROVIDER=colab
QWEN_SERVICE_URL=https://your-current-colab-tunnel.ngrok-free.app
QWEN_API_KEY=shared-demo-token
QWEN_TIMEOUT_SECONDS=600
QWEN_ALLOW_COMPANY_CONTEXT=true
QWEN_INTENT_CLASSIFY_ENABLED=true
QWEN_ACCIDENT_CLASSIFY_ENABLED=true
QWEN_LOAD_IN_4BIT=true
QWEN_MAX_NEW_TOKENS=256
QWEN_CLASSIFY_MAX_NEW_TOKENS=64
QWEN_SOURCE_EXCERPT_CHARS=180
QWEN_DOCUMENT_SOURCE_LIMIT=2
QWEN_COMPONENT_SOURCE_LIMIT=2
QWEN_MAINTENANCE_SOURCE_LIMIT=5
```

## 확인 순서

1. 최신 프로젝트 ZIP 또는 repo clone으로 Colab notebook을 실행합니다.
2. ngrok 셀이 통과하면 출력된 URL을 `.env`의 `QWEN_SERVICE_URL`에 넣습니다.
3. 웹 백엔드를 재시작합니다.
4. 문서 질문, 부품 질문, 유지보수 질문을 각각 실행합니다.
5. Colab 로그의 `qwen_generate`에서 `cuda_available`, `gpu_name`, `hf_device_map`, `is_loaded_in_4bit`, `input_tokens`, `new_tokens`, `elapsed_seconds`, `tokens_per_second`를 확인합니다.
6. 동일 질문 5회 측정으로 30초 안팎 유지 여부를 확인합니다.
