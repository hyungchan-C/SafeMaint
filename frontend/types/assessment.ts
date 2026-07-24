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
  chunk_id: string;
  title: string;
  page: number | null;
  page_start: number | null;
  page_end: number | null;
  section: string | null;
  source_type: string;
  document_scope: "public" | "company" | null;
  original_filename: string | null;
  document_version: number | null;
  excerpt: string;
  url: string | null;
  retrieval_rank: number;
  retrieval_score: number | null;
  reranker_score: number | null;
  used_in_answer: boolean;
}

export interface ChecklistItemResponse {
  id: string | null;
  sequence: number;
  content: string;
  is_completed: boolean;
  completed_by_user_id: string | null;
  completed_at: string | null;
}

export interface ChecklistItemUpdateResponse extends ChecklistItemResponse {
  id: string;
  assessment_id: string;
}

export interface AssessmentResponse {
  assessment_id: string;
  status: "draft" | "pending_review" | "approved" | "rejected";
  created_at: string;
  hazards: HazardItem[];
  tbm_checklist: string[];
  checklist_items: ChecklistItemResponse[];
  evidence: EvidenceItem[];
  evidence_status: "not_connected" | "connected";
  disclaimer: string;
}

export interface AssessmentSummaryResponse {
  assessment_id: string;
  status: "draft" | "pending_review" | "approved" | "rejected";
  created_at: string;
  created_by_user_id: string | null;
  created_by_name: string | null;
  site_name: string;
  equipment_name: string;
  task_type: string;
  description: string;
  checklist_total: number;
  checklist_completed: number;
}
