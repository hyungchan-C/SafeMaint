# SafeMaint Qwen 구조화 답변 API 전달 안내

팀원에게 아래 파일을 함께 전달해야 합니다.

- `notebooks/SafeMaint_Qwen35_9B_Colab_Server.ipynb`
- `ai/qwen_service/` 전체

노트북은 `ai/qwen_service`의 FastAPI 서버를 실행하는 런처입니다. 노트북 파일만
전달하면 새 API 계약이 적용되지 않으므로, 최신 프로젝트 ZIP을 업로드하거나 최신
저장소를 clone해야 합니다.

## 변경된 계약

### `POST /v1/classify`

기존 `occurrence_type`과 함께 다음 값을 반환합니다.

- `question_intent`: `document_qa`, `maintenance_guide`, `component_info`, `clarification_required`
- `intent_confidence`
- `clarification_question`
- 같은 값을 포함한 `analysis`

### `POST /v1/answer`

요청에 다음 필드가 추가됩니다.

- `answer_type`: `document_qa`, `maintenance_guide`, `component_info`

응답은 기존 `answer`, `model`을 유지하고 다음 필드를 추가합니다.

- `answer_type`
- `structured_answer`
- `checklist_items`
- `used_source_ids`

`used_source_ids`와 각 항목의 `evidence_chunk_ids`에는 요청으로 전달받은 `chunk_id`만
사용해야 합니다. 유지보수 작업 절차는 `manual`, `equipment_manual`,
`component_manual`, `work_standard` 근거가 있을 때만 생성합니다.

위험도 `risk_level`을 `판단 불가`가 아닌 값으로 반환하려면 `risk_basis`를 문자열이
아닌 `content`와 `evidence_chunk_ids`를 가진 근거 객체 배열로 반환해야 합니다.
유효한 검색 청크 근거가 없으면 반드시 `risk_level=판단 불가`, `risk_basis=[]`로
반환합니다. 백엔드도 이 조건을 다시 검증하고 충족하지 않으면 `판단 불가`로
강제합니다.

서로 충돌하는 근거가 실제로 있을 때만 `conflicts`에 항목을 추가합니다. 각 충돌
항목은 `content`와 서로 다른 검색 청크 ID 두 개 이상을 포함해야 하며, 근거 간
차이가 없으면 `conflicts=[]`로 반환합니다.

## 확인 순서

1. 최신 프로젝트 ZIP 또는 저장소를 Colab에 준비합니다.
2. 노트북을 위에서부터 실행합니다.
3. `Structured answer contract smoke test` 셀을 실행합니다.
4. component/maintenance 관련 모든 assertion이 통과한 뒤 ngrok URL을 팀원에게 공유합니다.

백엔드는 이전 Qwen 서버가 문자열 `answer`만 반환해도 기존 답변을 표시합니다. 새
구조화 JSON이 잘못되면 API를 500으로 종료하지 않고 검증된 검색 근거 기반 fallback을
사용합니다.
