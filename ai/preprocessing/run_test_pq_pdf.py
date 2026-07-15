"""
새로 추가된 PQ 시리즈 PDF 2개를 처리하는 1회성 테스트 스크립트.
autonics_chunks.json(BTS/AK-2/MD2U-ID20/MSO-SFL)과 섞이지 않도록 별도 파일에 저장한다.
"""

import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from pdf_pipeline import process_pdf

RAW_DIR = os.path.join(BASE_DIR, "..", "data", "raw")
PROCESSED_DIR = os.path.join(BASE_DIR, "..", "data", "processed")

files = [
    {
        "path": os.path.join(RAW_DIR, "PQ_manual_KR.pdf"),
        "product_type": "포토센서",
        "model_name": "PQ Series",
    },
    {
        "path": os.path.join(RAW_DIR, "PQ_series_230526.pdf"),
        "product_type": "포토센서",
        "model_name": "PQ Series",
    },
]

all_chunks = []
for f in files:
    chunks = process_pdf(
        pdf_path=f["path"],
        product_type=f["product_type"],
        model_name=f["model_name"],
    )
    print(f"{os.path.basename(f['path'])}: {len(chunks)}개 청크 생성")
    all_chunks.extend(chunks)

print(f"\n총 청크 수: {len(all_chunks)}")

os.makedirs(PROCESSED_DIR, exist_ok=True)
out_path = os.path.join(PROCESSED_DIR, "test_pq_chunks.json")
with open(out_path, "w", encoding="utf-8") as fp:
    json.dump(all_chunks, fp, ensure_ascii=False, indent=2)
print(f"저장 완료: {out_path}")
