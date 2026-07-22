export type DocumentLifecycleStatus =
  | "pending"
  | "processing"
  | "review_required"
  | "active"
  | "failed"
  | "deleted";

export type DocumentVersionStatus = DocumentLifecycleStatus | "superseded" | "ocr_required";

export interface UserDocumentSummary {
  document_id: string;
  document_version_id: string;
  original_filename: string;
  title: string;
  version_number: number;
  document_type_code: string;
  source_type: string;
  access_level: string;
  lifecycle_status: DocumentLifecycleStatus;
  status: DocumentVersionStatus;
  is_active: boolean;
  extractor: string | null;
  fallback_used: boolean;
  processing_warning: string | null;
  failure_reason: string | null;
  page_count: number | null;
  created_at: string;
}

export interface ReviewQueueDocument {
  document_id: string;
  document_version_id: string;
  original_filename: string;
  title: string;
  version_number: number;
  document_type_code: string;
  source_type: string;
  access_level: string;
  lifecycle_status: DocumentLifecycleStatus;
  version_status: DocumentVersionStatus;
  is_active: boolean;
  uploaded_by_user_id: string | null;
  uploader_name: string | null;
  created_at: string;
  extractor: string | null;
  fallback_used: boolean;
  processing_warning: string | null;
  failure_reason: string | null;
  page_count: number | null;
}

export interface ApproveDocumentResponse {
  document_id: string;
  document_version_id: string;
  version_number: number;
  status: "active";
  is_active: true;
}

export const DOCUMENT_STATUS_LABELS: Record<DocumentVersionStatus, string> = {
  pending: "처리 대기",
  processing: "PDF 처리 중",
  review_required: "승인 대기",
  active: "승인 완료 · RAG 검색 가능",
  failed: "처리 실패",
  superseded: "이전 버전",
  deleted: "삭제됨",
  ocr_required: "OCR 처리 필요",
};
