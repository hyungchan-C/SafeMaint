# SafeMaint Qwen Fast API Handoff

Colab에 올리는 프로젝트 ZIP에는 반드시 최신 `ai/qwen_service/`가 들어가야 합니다.

## 기본 속도 모드

현재 기본값은 빠른 답변 모드입니다.

- `QWEN_LOAD_IN_4BIT=true`
- `QWEN_ANSWER_MODE=text`
- `QWEN_REPAIR_ENABLED=false`
- `QWEN_MAX_NEW_TOKENS=256`
- `QWEN_CLASSIFY_MAX_NEW_TOKENS=64`

이 모드에서 `POST /v1/answer`는 짧은 자연어 `answer`만 Qwen으로 생성합니다.
`structured_answer`는 `null`, `checklist_items`는 빈 배열로 반환될 수 있습니다. 정상입니다.

웹 백엔드는 Qwen 답변을 받은 뒤, 이미 검색된 RAG source를 기준으로 `structured_answer`와
`checklist_items`를 `source_based_fallback` / `enriched_structured_answer` 로 채웁니다.
따라서 화면의 구조화 UI는 유지하면서 Qwen이 긴 JSON을 생성하는 시간을 줄입니다.

## 느린 구조화 모드

디버깅이나 비교 실험이 필요할 때만 다음처럼 켭니다.

- `QWEN_ANSWER_MODE=structured`
- `QWEN_REPAIR_ENABLED=true`
- `QWEN_MAX_NEW_TOKENS=768` 이상

이 모드는 Qwen이 `structured_answer` JSON 전체를 생성하고, 실패하면 repair 생성을 추가로 수행할 수 있어
응답 시간이 크게 늘어납니다.

## 백엔드 설정

Colab ngrok URL을 받은 뒤 각자 `.env`에는 다음 값을 맞춥니다.

```env
QWEN_ENABLED=true
QWEN_PROVIDER=colab
QWEN_SERVICE_URL=https://your-current-colab-tunnel.ngrok-free.app
QWEN_API_KEY=shared-demo-token
QWEN_TIMEOUT_SECONDS=600
QWEN_ALLOW_COMPANY_CONTEXT=true
QWEN_INTENT_CLASSIFY_ENABLED=false
```

`QWEN_INTENT_CLASSIFY_ENABLED=false`는 `/v1/answer` 전에 Qwen 서비스의 `/v1/classify`를
추가 호출하지 않게 하는 옵션입니다. 별도 LoRA 사고유형 분류 서비스인
`QWEN_CLASSIFIER_ENABLED`와는 다른 옵션입니다.

## 확인 순서

1. 최신 프로젝트 ZIP 또는 repo clone으로 Colab notebook을 실행합니다.
2. 첫 설정 셀에서 `QWEN_ANSWER_MODE=text`, `QWEN_MAX_NEW_TOKENS=256`인지 확인합니다.
3. ngrok 셀이 통과하면 URL을 공유해도 됩니다.
4. 필요할 때만 `Optional fast answer smoke test` 셀로 `/v1/answer`를 워밍업합니다.
5. Colab 로그의 `qwen_generate`에서 `cuda_available`, `gpu_name`, `hf_device_map`,
   `is_loaded_in_4bit`, `input_tokens`, `new_tokens`, `elapsed_seconds`,
   `tokens_per_second`를 확인합니다.
6. 웹 백엔드를 재시작한 뒤 동일 질문을 다시 5회 측정합니다.
