from __future__ import annotations

from dataclasses import dataclass, replace
from math import asin, cos, radians, sin, sqrt

from app.core.config import settings
from app.schemas.assessment import AssessmentRequest, HazardItem
from app.services.risk_engine import RiskEngine

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True, slots=True)
class VirtualEquipmentLocation:
    equipment_code: str
    site_name: str
    equipment_name: str
    equipment_type: str
    manufacturer: str | None
    model_number: str | None
    latitude: float
    longitude: float


# 실제 GPS 연동 전까지 사용하는 테스트용 목데이터.
# DB의 Equipment 테이블(팀원 담당)과는 별개이며, 실연동 시 좌표 컬럼으로 이관 예정.
VIRTUAL_EQUIPMENT_LOCATIONS: tuple[VirtualEquipmentLocation, ...] = (
    VirtualEquipmentLocation(
        equipment_code="CONV-203",
        site_name="A공장",
        equipment_name="컨베이어 CV-203",
        equipment_type="컨베이어",
        manufacturer="테스트 제조사",
        model_number="CV-203",
        latitude=37.566500,
        longitude=126.978000,
    ),
    VirtualEquipmentLocation(
        equipment_code="PNL-01",
        site_name="A공장",
        equipment_name="전기 판넬 PNL-01",
        equipment_type="전기설비",
        manufacturer="테스트 제조사",
        model_number="PNL-01",
        latitude=37.566700,
        longitude=126.978200,
    ),
    VirtualEquipmentLocation(
        equipment_code="WLD-05",
        site_name="A공장",
        equipment_name="용접기 WLD-05",
        equipment_type="용접기",
        manufacturer="테스트 제조사",
        model_number="WLD-05",
        latitude=37.566300,
        longitude=126.977800,
    ),
    VirtualEquipmentLocation(
        equipment_code="CONV-101",
        site_name="B공장",
        equipment_name="컨베이어 CV-101",
        equipment_type="컨베이어",
        manufacturer="테스트 제조사",
        model_number="CV-101",
        latitude=37.570000,
        longitude=126.980000,
    ),
)


# 목데이터 좌표 전체를 이 지점을 기준으로 한 상대 배치로 취급한다.
# 실사용자가 실제 GPS로 걸어다니며 테스트할 수 있도록, 이 원점을 사용자의
# 실제 최초 위치로 옮기면(캘리브레이션) 전체 설비가 그 자리 그대로 함께 이동한다.
CANONICAL_ORIGIN = (
    VIRTUAL_EQUIPMENT_LOCATIONS[0].latitude,
    VIRTUAL_EQUIPMENT_LOCATIONS[0].longitude,
)

METERS_PER_DEG_LAT = 111_320.0


def _meters_per_deg_lon(lat_deg: float) -> float:
    return METERS_PER_DEG_LAT * cos(radians(lat_deg))


def resolve_locations(
    origin: tuple[float, float] | None = None,
) -> tuple[VirtualEquipmentLocation, ...]:
    if origin is None:
        return VIRTUAL_EQUIPMENT_LOCATIONS

    origin_lat, origin_lon = origin
    canonical_lat, canonical_lon = CANONICAL_ORIGIN

    result = []
    for location in VIRTUAL_EQUIPMENT_LOCATIONS:
        # 표준 원점 대비 상대 위치를 미터 단위로 구한 뒤, 새 원점의 위도를 기준으로
        # 다시 위경도로 환산한다. 위도가 달라지면 경도 1도당 거리도 달라지므로,
        # 단순히 위경도 차이를 그대로 더하면 위도가 먼 지점으로 옮길 때 상대
        # 배치(설비 간 거리)가 어긋난다.
        offset_x_m = (location.longitude - canonical_lon) * _meters_per_deg_lon(canonical_lat)
        offset_y_m = (location.latitude - canonical_lat) * METERS_PER_DEG_LAT
        result.append(
            replace(
                location,
                latitude=origin_lat + offset_y_m / METERS_PER_DEG_LAT,
                longitude=origin_lon + offset_x_m / _meters_per_deg_lon(origin_lat),
            )
        )
    return tuple(result)


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lon1_r, lat2_r, lon2_r = (radians(value) for value in (lat1, lon1, lat2, lon2))
    d_lat = lat2_r - lat1_r
    d_lon = lon2_r - lon1_r
    a = sin(d_lat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


@dataclass(frozen=True, slots=True)
class NearbyEquipment:
    location: VirtualEquipmentLocation
    distance_m: float


def find_nearby_equipment(
    latitude: float,
    longitude: float,
    radius_m: float | None = None,
    origin: tuple[float, float] | None = None,
) -> list[NearbyEquipment]:
    effective_radius = radius_m if radius_m is not None else settings.gps_proximity_radius_m

    nearby: list[NearbyEquipment] = []
    for location in resolve_locations(origin):
        distance = haversine_distance_m(
            latitude, longitude, location.latitude, location.longitude
        )
        if distance <= effective_radius:
            nearby.append(NearbyEquipment(location=location, distance_m=distance))

    nearby.sort(key=lambda entry: entry.distance_m)
    return nearby


_RISK_ENGINE = RiskEngine()


def build_checklist(location: VirtualEquipmentLocation) -> tuple[list[HazardItem], list[str]]:
    request = AssessmentRequest(
        site_name=location.site_name,
        equipment_name=location.equipment_name,
        manufacturer=location.manufacturer,
        model_number=location.model_number,
        task_type="정기 순찰 점검",
        description=(
            f"{location.equipment_name} 설비 근처에 접근했습니다. "
            "작업 전 안전수칙을 확인하세요."
        ),
    )
    return _RISK_ENGINE.evaluate(request)
