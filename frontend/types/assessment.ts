export type RiskLevel = "low" | "medium" | "high";

export interface HazardItem {
  name: string;
  accident_type: string;
  likelihood: number;
  severity: number;
  score: number;
  risk_level: RiskLevel;
  safety_actions: string[];
}

export interface EvidenceItem {
  document_id: string;
  title: string;
  page: number | null;
  source_type: string;
  excerpt: string;
  url: string | null;
}

export interface AssessmentResponse {
  assessment_id: string;
  status: "draft";
  created_at: string;
  hazards: HazardItem[];
  tbm_checklist: string[];
  evidence: EvidenceItem[];
  evidence_status: "not_connected" | "connected";
  disclaimer: string;
}
