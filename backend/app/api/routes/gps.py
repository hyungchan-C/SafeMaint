from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.schemas.gps import GpsCheckRequest, GpsCheckResponse, NearbyEquipmentItem
from app.services.virtual_gps import (
    build_checklist,
    determine_required_ppe,
    find_nearby_equipment,
    load_virtual_equipment_locations,
    resolve_locations,
)

router = APIRouter(prefix="/gps", tags=["gps"])

TEST_PAGE_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>가상 GPS 테스트</title>
<style>
  body { font-family: sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; color: #222; }
  button { display: block; width: 100%; margin: 8px 0; padding: 12px; font-size: 16px; cursor: pointer; }
  #result { margin-top: 24px; white-space: pre-wrap; background: #f4f4f4; padding: 12px; border-radius: 6px; min-height: 24px; }
  .item { margin-bottom: 12px; }
</style>
</head>
<body>
<h1>가상 GPS 테스트</h1>
<p>버튼을 누르면 해당 설비 앞으로 "이동"한 것처럼 시뮬레이션해서 안전 체크리스트를 보여줍니다.</p>
<div id="buttons">불러오는 중...</div>
<div id="result"></div>
<script>
async function loadEquipment() {
  const res = await fetch('/api/v1/gps/equipment');
  const equipment = await res.json();
  const container = document.getElementById('buttons');
  container.innerHTML = '';
  equipment.forEach((eq) => {
    const button = document.createElement('button');
    button.textContent = eq.site_name + ' - ' + eq.equipment_name + ' 앞으로 이동';
    button.onclick = function () {
      checkLocation(eq.latitude, eq.longitude);
    };
    container.appendChild(button);
  });
}

async function checkLocation(latitude, longitude) {
  const resultEl = document.getElementById('result');
  resultEl.textContent = '확인 중...';
  const res = await fetch('/api/v1/gps/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ latitude: latitude, longitude: longitude }),
  });
  const data = await res.json();
  if (!data.nearby.length) {
    resultEl.textContent = '근처에 설비가 없습니다.';
    return;
  }
  resultEl.innerHTML = data.nearby.map(function (item) {
    return (
      '<div class="item"><strong>' + item.equipment_name + '</strong> (' + item.distance_m + 'm)<br/>' +
      '위험요소: ' + item.hazards.map(function (h) { return h.name; }).join(', ') + '<br/>' +
      '체크리스트:<br/>' +
      item.checklist.map(function (c) { return '- ' + c; }).join('<br/>') +
      '</div>'
    );
  }).join('<hr/>');
}

loadEquipment();
</script>
</body>
</html>"""


@router.post("/check", response_model=GpsCheckResponse)
def check_location(
    payload: GpsCheckRequest,
    db: Annotated[Session, Depends(get_db)],
) -> GpsCheckResponse:
    """가상 GPS 좌표를 받아 근접한 설비의 안전 체크리스트를 반환합니다.

    origin_latitude/origin_longitude가 함께 전달되면, 실제 사용자의 최초 위치를
    기준점 삼아 가상 설비 전체를 그 주변으로 옮겨서 판정한다(실제 GPS로 걸어다니며
    테스트할 수 있도록 하는 캘리브레이션).
    """

    radius_m = payload.radius_m or settings.gps_proximity_radius_m
    origin = (
        (payload.origin_latitude, payload.origin_longitude)
        if payload.origin_latitude is not None and payload.origin_longitude is not None
        else None
    )
    locations = load_virtual_equipment_locations(db)
    nearby = find_nearby_equipment(payload.latitude, payload.longitude, locations, radius_m, origin)

    items = []
    for entry in nearby:
        hazards, checklist = build_checklist(entry.location)
        items.append(
            NearbyEquipmentItem(
                equipment_code=entry.location.equipment_code,
                site_name=entry.location.site_name,
                equipment_name=entry.location.equipment_name,
                equipment_type=entry.location.equipment_type,
                distance_m=round(entry.distance_m, 1),
                hazards=hazards,
                checklist=checklist,
                required_ppe=determine_required_ppe(entry.location),
            )
        )

    return GpsCheckResponse(
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_m=radius_m,
        nearby=items,
    )


@router.get("/test", response_class=HTMLResponse)
def gps_test_page() -> HTMLResponse:
    """설비별 버튼을 눌러 좌표 입력 없이 시나리오를 시험해보는 데모 페이지."""

    return HTMLResponse(TEST_PAGE_HTML)


@router.get("/equipment")
def list_virtual_equipment(
    db: Annotated[Session, Depends(get_db)],
    origin_latitude: float | None = Query(default=None, ge=-90, le=90),
    origin_longitude: float | None = Query(default=None, ge=-180, le=180),
) -> list[dict]:
    """DB에 좌표가 등록된 설비 목록.

    origin_latitude/origin_longitude를 전달하면, 그 위치를 기준으로 옮겨진
    좌표(캘리브레이션 결과)를 반환한다.
    """

    origin = (
        (origin_latitude, origin_longitude)
        if origin_latitude is not None and origin_longitude is not None
        else None
    )
    locations = load_virtual_equipment_locations(db)
    return [
        {
            "equipment_code": location.equipment_code,
            "site_name": location.site_name,
            "equipment_name": location.equipment_name,
            "equipment_type": location.equipment_type,
            "latitude": location.latitude,
            "longitude": location.longitude,
        }
        for location in resolve_locations(locations, origin)
    ]
