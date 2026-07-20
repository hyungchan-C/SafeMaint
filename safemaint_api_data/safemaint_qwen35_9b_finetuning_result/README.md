# SafeMaint Qwen3.5-9B 파인튜닝 결과

이 폴더는 팀원이 학습한 사고유형 분류 모델을 검증하기 위한 오프라인 실험
구조입니다. 현재 FastAPI 채팅 모델을 자동 교체하지 않으며, 어댑터 검증과 운영
승인이 끝날 때까지 기존 OpenAI 호환 provider 설정을 유지합니다.

## 실제 모델

`01_finetuned_model/best_adapter`가 학습된 LoRA Adapter다.

사용할 때 필요한 항목:

- 원본 모델: `Qwen/Qwen3.5-9B`
- Adapter: `01_finetuned_model/best_adapter`
- Processor/Tokenizer: `01_finetuned_model/tokenizer`
- 예제 코드: `01_finetuned_model/load_and_predict.py`

Adapter와 Tokenizer는 크기가 큰 모델 산출물이므로 Git에 포함하지 않습니다.
각 폴더가 없는 상태에서 로더는 명확한 오류로 종료되며 모델을 임의로 실행하지
않습니다. Python 모듈을 import하는 것만으로 모델을 로드하지 않고, 아래 명령을
실행할 때만 GPU 메모리를 사용합니다.

```powershell
python 01_finetuned_model/load_and_predict.py `
  --title "컨베이어 점검 중 손 끼임" `
  --text "가동 중인 롤러를 점검하다 손이 말려 들어갔다."
```

`trust_remote_code`는 기본적으로 꺼져 있습니다. 검토된 모델 코드가 반드시 필요한
경우에만 `--allow-remote-code`를 명시합니다. 모델 ID, 라벨, 최대 입력 길이와 생성
길이는 `model_manifest.json`에서 읽습니다.

## 폴더

### 01_finetuned_model

나중에 실제 추론에 사용하는 Adapter와 Tokenizer가 들어 있다.

### 02_finetuning_results

하이퍼파라미터 탐색, Validation 성능, 학습 이력이 들어 있다.

### 03_base_vs_finetuned_comparison

동일 Test에서 파인튜닝 전 Base와 파인튜닝 후 모델을 비교한 결과다.

- `base_model`: Base Test 결과
- `finetuned_model`: Fine-tuned Test 결과
- `comparison`: 두 결과의 차이

## 선택 기준

1. Validation Macro-F1
2. 희소 라벨 평균 F1
3. Validation loss
4. Accuracy

Test는 설정 잠금 이후 한 번만 사용했다.
