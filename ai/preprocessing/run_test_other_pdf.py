"""
오토닉스 외 다른 PDF(7-3-h.pdf, 국가기술자격 실기 시험문제)로 일반화 여부를 확인하기 위한
1회성 테스트 스크립트. 결과는 autonics_chunks.json과 섞이지 않도록 별도 파일에 저장한다.
"""

import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from pdf_pipeline import process_pdf

RAW_DIR = os.path.join(BASE_DIR, "..", "data", "raw")
PROCESSED_DIR = os.path.join(BASE_DIR, "..", "data", "processed")

chunks = process_pdf(
    pdf_path=os.path.join(RAW_DIR, "7-3-h.pdf"),
    product_type="시험문제",
    model_name="정보기기운용기능사 7-3",
    manufacturer="한국산업인력공단",
    exclude_sections=[],  # 오토닉스 전용 제외 키워드는 이 문서와 무관하므로 비움
)

print(f"7-3-h: {len(chunks)}개 청크 생성")

os.makedirs(PROCESSED_DIR, exist_ok=True)
out_path = os.path.join(PROCESSED_DIR, "test_other_pdf_chunks.json")
with open(out_path, "w", encoding="utf-8") as fp:
    json.dump(chunks, fp, ensure_ascii=False, indent=2)
print(f"저장 완료: {out_path}")

print("\n=== 전체 청크 미리보기 ===")
for c in chunks:
    print(json.dumps(c, ensure_ascii=False, indent=2)[:400])
    print("---")
