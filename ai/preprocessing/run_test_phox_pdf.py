"""
새로 추가된 LS메카피온 PHOX 시리즈(Xmotion AC 서보 드라이브) 매뉴얼 처리용 1회성 테스트 스크립트.
474페이지짜리 큰 문서로, 오토닉스 이외 제조사/제품군에 대한 일반화 검증용.
autonics_chunks.json 등 다른 결과와 섞이지 않도록 별도 파일에 저장한다.
"""

import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from pdf_pipeline import process_pdf

RAW_DIR = os.path.join(BASE_DIR, "..", "data", "raw")
PROCESSED_DIR = os.path.join(BASE_DIR, "..", "data", "processed")

PDF_PATH = os.path.join(RAW_DIR, "=_UTF-8_Q_[LSMecapion]_PHOX=5FKOR=5FVer1.6=5F260304.pdf")

chunks = process_pdf(
    pdf_path=PDF_PATH,
    product_type="AC서보드라이브",
    model_name="Xmotion PHOX Series",
    manufacturer="LS메카피온",
    exclude_sections=[],  # 오토닉스 전용 제외 키워드는 이 문서와 무관하므로 비움
)

print(f"PHOX: {len(chunks)}개 청크 생성")

os.makedirs(PROCESSED_DIR, exist_ok=True)
out_path = os.path.join(PROCESSED_DIR, "test_phox_chunks.json")
with open(out_path, "w", encoding="utf-8") as fp:
    json.dump(chunks, fp, ensure_ascii=False, indent=2)
print(f"저장 완료: {out_path}")
