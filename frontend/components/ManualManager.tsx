"use client";

import InterfaceIcon from "@/components/InterfaceIcon";
import type { UserDocumentSummary } from "@/types/documents";
import { DOCUMENT_STATUS_LABELS } from "@/types/documents";

type Props = {
  manuals: string[];
  documents: UserDocumentSummary[];
  selectedDocumentIds: string[];
  manualStatus: string;
  sitePhotoName: string;
  visionStatus: string;
  isVisionLoading: boolean;
  onAddManuals: (files: FileList | null) => void;
  onAddPhoto: (file: File | undefined) => void;
  onToggleDocument: (documentId: string) => void;
  onRemoveLegacyManual: (index: number) => void;
};

function documentAvailability(document: UserDocumentSummary): string {
  if (document.status === "active") return "RAG 검색 가능";
  if (document.status === "review_required") return "관리자 승인 필요";
  if (["pending", "processing"].includes(document.status)) return "PDF 처리 중";
  if (document.status === "failed") return "처리 실패";
  return DOCUMENT_STATUS_LABELS[document.status];
}

export default function ManualManager({
  manuals,
  documents,
  selectedDocumentIds,
  manualStatus,
  sitePhotoName,
  visionStatus,
  isVisionLoading,
  onAddManuals,
  onAddPhoto,
  onToggleDocument,
  onRemoveLegacyManual,
}: Props) {
  return (
    <section className="panel manual-manager" aria-labelledby="manual-manager-title">
      <div className="panel-heading workflow-panel-heading">
        <div><span className="section-number">01</span><div><h2 id="manual-manager-title">근거 자료 준비</h2><p>검색에 사용할 매뉴얼과 현장 사진을 선택합니다.</p></div></div>
        <span className="panel-tag muted">사진은 선택</span>
      </div>

      <div className="resource-actions">
        <label className="resource-upload primary-resource">
          <InterfaceIcon name="document" /><span><strong>PDF 매뉴얼 추가</strong><small>처리·승인 후 일반 검색 가능</small></span>
          <input type="file" accept="application/pdf" multiple onChange={(event) => onAddManuals(event.target.files)} />
        </label>
        <label className={isVisionLoading ? "resource-upload is-loading" : "resource-upload"}>
          <InterfaceIcon name="image" /><span><strong>{isVisionLoading ? "사진 분석 중" : "현장 사진 추가"}</strong><small>{sitePhotoName || "부품 외형 비교에 사용"}</small></span>
          <input type="file" accept="image/*" disabled={isVisionLoading} onChange={(event) => onAddPhoto(event.target.files?.[0])} />
        </label>
      </div>

      <div className="resource-status" role="status" aria-live="polite">
        <strong>{manuals.length ? `${manuals.length}개 매뉴얼 등록` : "등록된 매뉴얼 없음"}</strong>
        <span>{manualStatus || (sitePhotoName ? `현장 사진: ${sitePhotoName} · ${visionStatus}` : "문서를 추가하거나 기존 문서를 선택해 주세요.")}</span>
      </div>

      <div className="document-selection-list">
        {documents.length > 0
          ? documents.map((document) => {
              const isSelected = selectedDocumentIds.includes(document.document_id);
              return (
                <article className={`document-selection-card status-${document.status} ${isSelected ? "selected" : ""}`} key={document.document_version_id}>
                  <div className="document-selection-heading">
                    <InterfaceIcon name="document" />
                    <span><strong>{document.original_filename}</strong><small>버전 {document.version_number}</small></span>
                    <span className={`document-status status-${document.status}`}>{documentAvailability(document)}</span>
                  </div>
                  <div className="document-selection-meta">
                    <span>{DOCUMENT_STATUS_LABELS[document.status]}</span>
                    {document.fallback_used && <span className="fallback-label">PyMuPDF 대체 처리</span>}
                  </div>
                  {document.processing_warning && <p className="document-chip-warning">{document.processing_warning}</p>}
                  {document.failure_reason && <p className="document-chip-warning" role="alert">{document.failure_reason}</p>}
                  <button type="button" className={isSelected ? "document-select-button selected" : "document-select-button"} aria-pressed={isSelected} onClick={() => onToggleDocument(document.document_id)}>
                    {isSelected ? "검색 범위에 포함됨" : "검색 범위에 추가"}
                  </button>
                </article>
              );
            })
          : manuals.map((name, index) => (
              <span className="legacy-document-chip" key={`${name}-${index}`}><InterfaceIcon name="document" />{name}<button type="button" aria-label={`${name} 선택 해제`} onClick={() => onRemoveLegacyManual(index)}>×</button></span>
            ))}
        {documents.length === 0 && manuals.length === 0 && (
          <div className="resource-empty"><InterfaceIcon name="document" /><strong>선택된 문서가 없습니다.</strong><span>매뉴얼 없이도 공용 안전자료로 질문할 수 있습니다.</span></div>
        )}
      </div>
    </section>
  );
}
