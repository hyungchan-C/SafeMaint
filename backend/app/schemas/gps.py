from pydantic import BaseModel, Field

from app.schemas.assessment import HazardItem


class GpsCheckRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float | None = Field(default=None, gt=0, le=5000)
    origin_latitude: float | None = Field(default=None, ge=-90, le=90)
    origin_longitude: float | None = Field(default=None, ge=-180, le=180)


class NearbyEquipmentItem(BaseModel):
    equipment_code: str
    site_name: str
    equipment_name: str
    equipment_type: str
    distance_m: float
    hazards: list[HazardItem]
    checklist: list[str]


class GpsCheckResponse(BaseModel):
    latitude: float
    longitude: float
    radius_m: float
    nearby: list[NearbyEquipmentItem]
