export interface ChatSource {
  document_id: string;
  document_version_id: string | null;
  chunk_id: string;
  title: string;
  source_type: string;
  document_scope: "public" | "company" | null;
  original_filename: string | null;
  document_version: number | null;
  section: string | null;
  excerpt: string;
  page: number | null;
  page_start: number | null;
  page_end: number | null;
  publisher: string | null;
  url: string | null;
  similarity: number;
  keyword_score: number;
  retrieval_score: number;
  reranker_score: number;
}

export type AnswerType =
  | "document_qa"
  | "maintenance_guide"
  | "component_info"
  | "no_evidence"
  | "clarification_required";

export interface EvidenceBackedItem {
  content: string;
  evidence_chunk_ids: string[];
}

export interface EvidenceConflict {
  content: string;
  evidence_chunk_ids: string[];
}

export interface DocumentAnswerDetails {
  answer_type: "document_qa";
  overview: {
    filename: string | null;
    document_type: string | null;
    manufacturer: string | null;
    model_name: string | null;
    version: string | null;
    authored_at: string | null;
  };
  main_contents: EvidenceBackedItem[];
  related_equipment: string[];
  related_components: string[];
  supported_tasks: string[];
  evidence_chunk_ids: string[];
  conflicts: EvidenceConflict[];
  unverified_information: string[];
}

export interface MaintenanceAnswerDetails {
  answer_type: "maintenance_guide";
  summary: {
    status: "안전관리자 확인 필요" | "작업 중지 권고" | "근거 부족";
    risk_level: "낮음" | "보통" | "높음" | "매우 높음" | "판단 불가";
    risk_basis: EvidenceBackedItem[];
    core_warning: string;
  };
  pre_checks: EvidenceBackedItem[];
  hazards: Array<EvidenceBackedItem & { name: string }>;
  manual_steps: EvidenceBackedItem[];
  stop_conditions: EvidenceBackedItem[];
  related_regulations_and_incidents: EvidenceBackedItem[];
  evidence_chunk_ids: string[];
  conflicts: EvidenceConflict[];
  additional_information_needed: string[];
}

export interface ComponentAnswerDetails {
  answer_type: "component_info";
  one_line_description: string;
  main_roles: EvidenceBackedItem[];
  usage_locations: EvidenceBackedItem[];
  precautions: EvidenceBackedItem[];
  evidence_chunk_ids: string[];
  conflicts: EvidenceConflict[];
  additional_information_needed: string[];
}

export interface NoEvidenceDetails {
  answer_type: "no_evidence";
  message: string;
  required_information: string[];
  required_documents: string[];
  work_safety_notice: string | null;
}

export interface ClarificationDetails {
  answer_type: "clarification_required";
  question: string;
  options: string[];
}

export type StructuredAnswer =
  | DocumentAnswerDetails
  | MaintenanceAnswerDetails
  | ComponentAnswerDetails
  | NoEvidenceDetails
  | ClarificationDetails;

export interface ChatChecklistItem {
  id: string | null;
  content: string;
  sequence: number;
  is_required: boolean;
  is_completed: boolean;
  completed_by_user_id: string | null;
  completed_at: string | null;
  evidence_chunk_ids: string[];
}

export interface ChatResponse {
  answer: string;
  answer_type: AnswerType | null;
  structured_answer: StructuredAnswer | null;
  checklist_items: ChatChecklistItem[];
  clarification_question: string | null;
  sources: ChatSource[];
  retrieval_mode: "bge-m3" | "hybrid" | "safety-fallback";
  generation_mode: "openai" | "template" | "qwen";
  model: string | null;
  warning: string | null;
  accident_classification: AccidentClassification | null;
}

export interface AccidentClassification {
  label: string;
  model: string;
  adapter: string;
}

export interface CatalogCandidate {
  document_id: string;
  filename: string;
  page: number;
  image_index: number;
  similarity: number;
  confidence: string;
  note: string;
  visual_category?: string | null;
  visual_features?: string[];
  page_excerpt?: string | null;
}

export interface ChatMessage {
  role: "user" | "ai";
  text: string;
  sourceQuestion?: string;
  answerType?: AnswerType | null;
  structuredAnswer?: StructuredAnswer | null;
  checklistItems?: ChatChecklistItem[];
  clarificationQuestion?: string | null;
  sources?: ChatSource[];
  retrievalMode?: ChatResponse["retrieval_mode"];
  generationMode?: ChatResponse["generation_mode"];
  model?: string | null;
  warning?: string | null;
  accidentClassification?: AccidentClassification | null;
  catalogCandidates?: CatalogCandidate[];
}
