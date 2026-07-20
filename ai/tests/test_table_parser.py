import json

from vision_service.table_parser import extract_table_rows


def test_extract_table_rows_expands_rowspan_cells() -> None:
    payload = {
        "res": {
            "parsing_res_list": [{
                "block_label": "table",
                "block_content": (
                    "<table><tr><td>종류</td><td>화소 수</td><td>색상</td><td>형식</td></tr>"
                    '<tr><td rowspan="2">CCD 카메라</td><td rowspan="2">30만 화소</td>'
                    "<td>컬러</td><td>FZ-SC</td></tr>"
                    "<tr><td>흑백</td><td>FZ-S</td></tr></table>"
                ),
            }]
        }
    }

    rows = extract_table_rows(json.dumps(payload, ensure_ascii=False))

    assert rows == [
        {"종류": "CCD 카메라", "화소 수": "30만 화소", "색상": "컬러", "형식": "FZ-SC"},
        {"종류": "CCD 카메라", "화소 수": "30만 화소", "색상": "흑백", "형식": "FZ-S"},
    ]
