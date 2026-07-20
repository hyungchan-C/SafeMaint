import type { HazardItem } from "./assessment";

export interface VirtualEquipment {
  equipment_code: string;
  site_name: string;
  equipment_name: string;
  equipment_type: string;
  latitude: number;
  longitude: number;
}

export interface NearbyEquipmentItem {
  equipment_code: string;
  site_name: string;
  equipment_name: string;
  equipment_type: string;
  distance_m: number;
  hazards: HazardItem[];
  checklist: string[];
}

export interface GpsCheckResponse {
  latitude: number;
  longitude: number;
  radius_m: number;
  nearby: NearbyEquipmentItem[];
}
