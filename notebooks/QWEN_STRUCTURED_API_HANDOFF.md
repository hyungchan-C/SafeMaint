# SafeMaint Qwen API 전달 안내

Colab에 올리는 프로젝트 ZIP에는 반드시 최신 `ai/qwen_service/`가 들어가야 합니다.

## 운영 기본: 근거 구조화 모드

저장소와 Docker의 기본값은 질문 의도 분류와 근거 검증을 유지하는 구조화 모드입니다.

- `QWEN_LOAD_IN_4BIT=true`
- `QWEN_ANSWER_MODE=structured`
- `QWEN_REPAIR_ENABLED=true`
- `QWEN_MAX_NEW_TOKENS=768`
- `QWEN_CLASSIFY_MAX_NEW_TOKENS=64`
- `QWEN_INTENT_CLASSIFY_ENABLED=true`

이 모드는 `POST /v1/classify`로 질문 의도를 분류하고, `POST /v1/answer`에서
근거 청크 ID가 연결된 `structured_answer`와 `checklist_items`를 생성합니다.
생성한 출처 ID는 백엔드가 실제 검색 결과와 다시 대조합니다.

## 선택 사항: 빠른 자연어 모드

Colab 데모에서 속도를 우선해야 할 때만 다음처럼 전환합니다.

- `QWEN_ANSWER_MODE=text`
- `QWEN_REPAIR_ENABLED=false`
- `QWEN_MAX_NEW_TOKENS=256`
- 필요할 때만 `QWEN_INTENT_CLASSIFY_ENABLED=false`
- `QWEN_SOURCE_EXCERPT_CHARS=240`
- `QWEN_DOCUMENT_SOURCE_LIMIT=2`
- `QWEN_COMPONENT_SOURCE_LIMIT=2`
- `QWEN_MAINTENANCE_SOURCE_LIMIT=2`

이 모드에서 Qwen은 짧은 자연어 `answer`를 생성합니다. 각 근거 문장에는 반드시
`[1]`, `[2]` 같은 검색 근거 번호가 있어야 합니다. 인용이 없거나 검색되지 않은
근거를 가리키면 백엔드는 Qwen 자연어 답변을 버리고 RAG 기본 답변을 표시합니다.
구조화 영역은 이미 검증된 RAG 검색 결과의 미리보기를 사용합니다.

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
```

`QWEN_INTENT_CLASSIFY_ENABLED=false`는 `/v1/answer` 전에 Qwen 서비스의 `/v1/classify`를
추가 호출하지 않게 하는 옵션입니다. 별도 LoRA 사고유형 분류 서비스인
`QWEN_CLASSIFIER_ENABLED`와는 다른 옵션입니다.

## 확인 순서

1. 최신 프로젝트 ZIP 또는 repo clone으로 Colab notebook을 실행합니다.
2. 정확도 점검은 `structured/true/768`, 속도 점검은 `text/false/256`으로 설정합니다.
3. ngrok 셀이 통과하면 URL을 공유해도 됩니다.
4. 필요할 때만 `Optional fast answer smoke test` 셀로 `/v1/answer`를 워밍업합니다.
5. Colab 로그의 `qwen_generate`에서 `cuda_available`, `gpu_name`, `hf_device_map`,
   `is_loaded_in_4bit`, `input_tokens`, `new_tokens`, `elapsed_seconds`,
   `tokens_per_second`를 확인합니다.
6. 웹 백엔드를 재시작한 뒤 동일 질문을 다시 5회 측정합니다.
