"""
오토닉스 매뉴얼 4개를 전처리 -> 임베딩 -> DB 적재까지 실행하는 예시 스크립트.
DATABASE_URL 환경변수(backend와 동일한 규칙)로 접속할 Postgres를 지정한다.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from ingest_pdf import ingest_pdf  # noqa: E402

RAW_DIR = os.path.join(BASE_DIR, "..", "data", "raw")

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

for f in files:
    result = ingest_pdf(
        pdf_path=f["path"],
        product_type=f["product_type"],
        model_name=f["model_name"],
    )
    print(f"{f['model_name']}: document_id={result['document_id']}, {result['chunk_count']}개 청크 적재 완료")
