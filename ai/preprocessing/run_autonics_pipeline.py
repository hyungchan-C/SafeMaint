"""
오토닉스 4개 매뉴얼을 pdf_pipeline.py의 재사용 함수로 처리.
이 스크립트는 '지금의 배치 처리'용이고,
같은 process_pdf() 함수가 나중에 F-01 업로드 기능에서도 그대로 재사용됩니다.
"""

import os
import sys
import json

# 이 스크립트 파일 기준 경로 (ai/preprocessing/)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)  # 같은 폴더의 pdf_pipeline.py를 import하기 위함

from pdf_pipeline import process_pdf

# 원본 PDF는 ai/data/raw/, 결과는 ai/data/processed/ 에 저장
RAW_DIR = os.path.join(BASE_DIR, "..", "data", "raw")
PROCESSED_DIR = os.path.join(BASE_DIR, "..", "data", "processed")

files = [
    {
        "path": os.path.join(RAW_DIR, "BTS_KO_TCD230005AD_MODI_20260610_MANUAL_W.pdf"),
        "product_type": "포토센서",
        "model_name": "BTS Series",
    },
    {
        "path": os.path.join(RAW_DIR, "AK-2_KO_TCD210131AC_MODI_20241118_MANUAL_W.pdf"),
        "product_type": "스테핑모터",
        "model_name": "AK-2 Series",
    },
    {
        "path": os.path.join(RAW_DIR, "MD2U-ID20_KO_TCD210132AB_20230712_MANUAL_W.pdf"),
        "product_type": "스테핑모터드라이버",
        "model_name": "MD2U-ID20 Series",
    },
    {
        "path": os.path.join(RAW_DIR, "MSO-SFL_A_U1-V3.1-KO_20250912_W.pdf"),
        "product_type": "라이트커튼(안전센서)",
        "model_name": "SFL/SFLA Series",
    },
]

all_documents = []
all_chunks = []
for f in files:
    result = process_pdf(
        pdf_path=f["path"],
        product_type=f["product_type"],
        model_name=f["model_name"],
    )
    print(f"{f['model_name']}: {len(result['chunks'])}개 청크 생성")
    all_documents.append(result["document"])
    all_chunks.extend(result["chunks"])

print(f"\n총 청크 수: {len(all_chunks)}")

# 결과 저장 (docs/preprocessing-contract.md의 JSONL 교환 규격)
os.makedirs(PROCESSED_DIR, exist_ok=True)

documents_path = os.path.join(PROCESSED_DIR, "autonics_documents.jsonl")
with open(documents_path, "w", encoding="utf-8") as fp:
    for doc in all_documents:
        fp.write(json.dumps(doc, ensure_ascii=False) + "\n")
print(f"저장 완료: {documents_path}")

chunks_path = os.path.join(PROCESSED_DIR, "autonics_chunks.jsonl")
with open(chunks_path, "w", encoding="utf-8") as fp:
    for chunk in all_chunks:
        fp.write(json.dumps(chunk, ensure_ascii=False) + "\n")
print(f"저장 완료: {chunks_path}")

# 샘플 3개 미리보기
print("\n=== 샘플 청크 미리보기 ===")
for c in all_chunks[:3]:
    print(json.dumps(c, ensure_ascii=False, indent=2)[:500])
    print("---")