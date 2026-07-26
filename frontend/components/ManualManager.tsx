"use client";

import { useState } from "react";

import InterfaceIcon from "@/components/InterfaceIcon";
import type { FileProcessingProgress, UserDocumentSummary } from "@/types/documents";
import { DOCUMENT_STATUS_LABELS } from "@/types/documents";

type Props = {
  manuals: string[];
  documents: UserDocumentSummary[];
  processingProgress?: FileProcessingProgress[];
  selectedDocumentIds: string[];
  manualStatus: string;
  sitePhotoName: string;
  visionStatus: string;
  visionElapsedLabel?: string | null;
  isVisionLoading: boolean;
  onAddManuals: (files: FileList | null) => void;
  onAddPhoto: (file: File | undefined) => void;
  onReindexDocument?: (documentId: string, filename: string) => void;
  onDeleteDocument?: (documentId: string, filename: string) => void;
  onToggleDocument: (documentId: string) => void;
  onRemoveLegacyManual: (index: number) => void;
  onRefreshDocuments?: () => void;
};

function documentAvailability(document: UserDocumentSummary): string {
  if (document.status === "active") return "RAG 검색 가능";
  if (document.status === "review_required") return "관리자 승인 필요";
  if (["pending", "processing"].includes(document.status)) return "PDF 처리 중";
  if (document.status === "failed") return "처리 실패";
  return DOCUMENT_STATUS_LABELS[document.status];
}

function ProgressDetails({ progress }: { progress: FileProcessingProgress }) {
  const counters = [
    progress.total_pages > 0
      ? `페이지 ${progress.processed_pages}/${progress.total_pages}`
      : null,
    progress.processed_chunks > 0 || progress.total_chunks > 0
      ? `청크 ${progress.processed_chunks}/${progress.total_chunks || "?"}`
      : null,
    progress.embedded_chunks > 0 || progress.stage === "embedding"
      ? `임베딩 ${progress.embedded_chunks}/${progress.total_chunks || "?"}`
      : null,
  ].filter(Boolean);
  const isWaitingInsideStage = (
    (progress.stage === "extracting" && progress.progress_percent === 20)
    || (progress.stage === "uploading" && progress.progress_percent === 10)
  );

  return (
    <div
      className={`document-progress status-${progress.status}`}
      aria-live="polite"
    >
      <div className="document-progress-heading">
        <strong>{progress.message}</strong>
        <span>{progress.progress_percent}%</span>
      </div>
      <progress
        max={100}
        value={progress.progress_percent}
        aria-label={`${progress.filename} 처리 진행률`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress.progress_percent}
      />
      {isWaitingInsideStage && (
        <span className="document-progress-activity" aria-hidden="true" />
      )}
      {counters.length > 0 && (
        <small>{counters.join(" · ")}</small>
      )}
    </div>
  );
}

export default function ManualManager({
  manuals,
  documents,
  processingProgress = [],
  selectedDocumentIds,
  manualStatus,
  sitePhotoName,
  visionStatus,
  visionElapsedLabel,
  isVisionLoading,
  onAddManuals,
  onAddPhoto,
  onReindexDocument,
  onDeleteDocument,
  onToggleDocument,
  onRemoveLegacyManual,
  onRefreshDocuments,
}: Props) {
  const [confirmingDelete, setConfirmingDelete] = useState<{ documentId: string; filename: string } | null>(null);
  const progressByDocument = new Map(
    processingProgress
      .filter((progress) => progress.document_id)
      .map((progress) => [progress.document_id, progress]),
  );
  const documentIds = new Set(documents.map((document) => document.document_id));
  const provisionalProgress = processingProgress.filter(
    (progress) => !progress.document_id || !documentIds.has(progress.document_id),
  );

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
        <span>{manualStatus || "문서를 추가하거나 기존 문서를 선택해 주세요."}</span>
        {sitePhotoName && <span>현장 사진: {sitePhotoName} · {visionStatus}</span>}
        {visionElapsedLabel && <small className="vision-elapsed">분석 소요 시간: {visionElapsedLabel}</small>}
        {onRefreshDocuments && (
          <button type="button" className="document-refresh-button" onClick={onRefreshDocuments}>
            문서 상태 새로고침
          </button>
        )}
      </div>

      <div className="document-selection-list">
        {provisionalProgress.map((progress) => (
          <article
            className={`document-selection-card status-${progress.status}`}
            key={progress.client_key}
          >
            <div className="document-selection-heading">
              <InterfaceIcon name="document" />
              <span>
                <strong title={progress.filename}>{progress.filename}</strong>
                <small>업로드 및 처리 준비</small>
              </span>
              <span className={`document-status status-${progress.status}`}>
                {progress.status === "failed" ? "업로드 실패" : "업로드 중"}
              </span>
            </div>
            <ProgressDetails progress={progress} />
          </article>
        ))}
        {documents.length > 0
          ? documents.map((document) => {
              const isSelected = selectedDocumentIds.includes(document.document_id);
              const progress = progressByDocument.get(document.document_id);
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
                  {progress && <ProgressDetails progress={progress} />}
                  <div className="document-selection-actions">
                    <button type="button" className={isSelected ? "document-select-button selected" : "document-select-button"} aria-pressed={isSelected} onClick={() => onToggleDocument(document.document_id)}>
                      {isSelected ? "검색 범위에 포함됨" : "검색 범위에 추가"}
                    </button>
                    {onReindexDocument && (
                      <button type="button" className="document-reindex-button" onClick={() => onReindexDocument(document.document_id, document.original_filename)}>
                        비전 재인덱싱
                      </button>
                    )}
                    {onDeleteDocument && (
                      <button
                        type="button"
                        className="document-delete-button"
                        onClick={() => setConfirmingDelete({ documentId: document.document_id, filename: document.original_filename })}
                      >
                        삭제
                      </button>
                    )}
                  </div>
                </article>
              );
            })
          : manuals.map((name, index) => (
              <span className="legacy-document-chip" key={`${name}-${index}`}><InterfaceIcon name="document" />{name}<button type="button" aria-label={`${name} 선택 해제`} onClick={() => onRemoveLegacyManual(index)}>×</button></span>
            ))}
        {documents.length === 0 && manuals.length === 0 && provisionalProgress.length === 0 && (
          <div className="resource-empty"><InterfaceIcon name="document" /><strong>선택된 문서가 없습니다.</strong><span>매뉴얼 없이도 공용 안전자료로 질문할 수 있습니다.</span></div>
        )}
      </div>

      {confirmingDelete && (
        <div className="document-confirm-backdrop" role="presentation">
          <div className="document-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="document-delete-title" aria-describedby="document-delete-description">
            <h3 id="document-delete-title">문서 삭제 확인</h3>
            <p id="document-delete-description">
              <strong>{confirmingDelete.filename}</strong>을(를) 삭제하시겠습니까?
              삭제하면 검색·승인 대상에서 제외되며, 되돌리려면 관리자에게 문의해야 합니다.
            </p>
            <div>
              <button type="button" onClick={() => setConfirmingDelete(null)}>취소</button>
              <button
                className="confirm-delete"
                type="button"
                onClick={() => {
                  onDeleteDocument?.(confirmingDelete.documentId, confirmingDelete.filename);
                  setConfirmingDelete(null);
                }}
              >
                삭제하기
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
