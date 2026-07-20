from __future__ import annotations

import argparse
import json

from app.core.config import settings
from app.services.virtual_gps import build_checklist, find_nearby_equipment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="가상 GPS 좌표를 입력해 근접 설비의 안전 체크리스트를 콘솔에 출력합니다."
    )
    parser.add_argument("--lat", type=float, required=True, help="위도")
    parser.add_argument("--lon", type=float, required=True, help="경도")
    parser.add_argument(
        "--radius-m",
        type=float,
        default=None,
        help=f"근접 판정 반경(m), 미지정 시 기본값 {settings.gps_proximity_radius_m}m",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    nearby = find_nearby_equipment(args.lat, args.lon, args.radius_m)

    entries = []
    for entry in nearby:
        hazards, checklist = build_checklist(entry.location)
        entries.append(
            {
                "equipment_code": entry.location.equipment_code,
                "site_name": entry.location.site_name,
                "equipment_name": entry.location.equipment_name,
                "distance_m": round(entry.distance_m, 1),
                "hazards": [hazard.name for hazard in hazards],
                "checklist": checklist,
            }
        )

    print(
        json.dumps(
            {
                "latitude": args.lat,
                "longitude": args.lon,
                "radius_m": args.radius_m or settings.gps_proximity_radius_m,
                "nearby": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
