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

type DocumentListFilter =
  | "all"
  | "selected"
  | "active"
  | "processing"
  | "review_required"
  | "failed";

type DocumentListItem =
  | {
      kind: "progress";
      key: string;
      filename: string;
      progress: FileProcessingProgress;
    }
  | {
      kind: "document";
      key: string;
      filename: string;
      document: UserDocumentSummary;
    }
  | {
      kind: "legacy";
      key: string;
      filename: string;
      index: number;
    };

const COLLAPSED_DOCUMENT_COUNT = 3;
const DOCUMENT_PAGE_SIZE = 10;

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
  const [documentQuery, setDocumentQuery] = useState("");
  const [documentFilter, setDocumentFilter] = useState<DocumentListFilter>("all");
  const [isDocumentListExpanded, setIsDocumentListExpanded] = useState(false);
  const [visibleDocumentCount, setVisibleDocumentCount] = useState(DOCUMENT_PAGE_SIZE);
  const progressByDocument = new Map(
    processingProgress
      .filter((progress) => progress.document_id)
      .map((progress) => [progress.document_id, progress]),
  );
  const documentIds = new Set(documents.map((document) => document.document_id));
  const provisionalProgress = processingProgress.filter(
    (progress) => !progress.document_id || !documentIds.has(progress.document_id),
  );
  const selectedDocumentIdSet = new Set(selectedDocumentIds);
  const orderedDocuments = [...documents].sort((left, right) => {
    const selectedOrder = Number(selectedDocumentIdSet.has(right.document_id))
      - Number(selectedDocumentIdSet.has(left.document_id));
    if (selectedOrder !== 0) return selectedOrder;
    return Date.parse(right.created_at) - Date.parse(left.created_at);
  });
  const documentListItems: DocumentListItem[] = [
    ...provisionalProgress.map((progress) => ({
      kind: "progress" as const,
      key: `progress-${progress.client_key}`,
      filename: progress.filename,
      progress,
    })),
    ...orderedDocuments.map((currentDocument) => ({
      kind: "document" as const,
      key: `document-${currentDocument.document_version_id}`,
      filename: currentDocument.original_filename,
      document: currentDocument,
    })),
    ...(documents.length === 0
      ? manuals.map((filename, index) => ({
          kind: "legacy" as const,
          key: `legacy-${filename}-${index}`,
          filename,
          index,
        }))
      : []),
  ];
  const normalizedDocumentQuery = documentQuery.trim().toLocaleLowerCase("ko-KR");
  const filteredDocumentItems = documentListItems.filter((item) => {
    if (
      normalizedDocumentQuery
      && !item.filename.toLocaleLowerCase("ko-KR").includes(normalizedDocumentQuery)
    ) {
      return false;
    }

    if (documentFilter === "all") return true;
    if (documentFilter === "selected") {
      return item.kind === "legacy"
        || (item.kind === "document" && selectedDocumentIdSet.has(item.document.document_id));
    }
    if (documentFilter === "active") {
      return item.kind === "document" && item.document.status === "active";
    }
    if (documentFilter === "processing") {
      return item.kind === "progress"
        || (
          item.kind === "document"
          && ["pending", "processing", "ocr_required"].includes(item.document.status)
        );
    }
    if (documentFilter === "review_required") {
      return item.kind === "document" && item.document.status === "review_required";
    }
    return (
      (item.kind === "progress" && item.progress.status === "failed")
      || (item.kind === "document" && item.document.status === "failed")
    );
  });
  const displayedDocumentItems = filteredDocumentItems.slice(
    0,
    isDocumentListExpanded ? visibleDocumentCount : COLLAPSED_DOCUMENT_COUNT,
  );
  const selectedDocumentNames = documents.length > 0
    ? orderedDocuments
        .filter((currentDocument) => selectedDocumentIdSet.has(currentDocument.document_id))
        .map((currentDocument) => currentDocument.original_filename)
    : manuals;
  const previewDocumentNames = documentListItems
    .slice(0, COLLAPSED_DOCUMENT_COUNT)
    .map((item) => item.filename);
  const hasMoreDocuments = visibleDocumentCount < filteredDocumentItems.length;
  const shouldShowDocumentDetails = isDocumentListExpanded || documentListItems.length <= 1;

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

      <section
        className={isDocumentListExpanded ? "document-browser expanded" : "document-browser"}
        aria-labelledby="document-browser-title"
      >
        <div className="document-browser-summary">
          <div>
            <strong id="document-browser-title">등록 문서</strong>
            <span>{documentListItems.length}개 · 검색 범위 {selectedDocumentNames.length}개</span>
          </div>
          {documentListItems.length > 1 && (
            <button
              type="button"
              className="document-list-toggle"
              aria-expanded={isDocumentListExpanded}
              aria-controls="registered-document-list"
              onClick={() => {
                setIsDocumentListExpanded((current) => !current);
                setVisibleDocumentCount(DOCUMENT_PAGE_SIZE);
              }}
            >
              {isDocumentListExpanded ? "− 목록 접기" : "＋ 목록 펼치기"}
            </button>
          )}
        </div>

        <div className="selected-document-summary" aria-label="최근 등록한 문서">
          <strong>등록 파일 미리보기</strong>
          {previewDocumentNames.length > 0 ? (
            <div>
              {previewDocumentNames.map((filename) => (
                <span title={filename} key={filename}>{filename}</span>
              ))}
              {documentListItems.length > COLLAPSED_DOCUMENT_COUNT && (
                <span>외 {documentListItems.length - COLLAPSED_DOCUMENT_COUNT}개</span>
              )}
            </div>
          ) : (
            <small>등록된 문서가 없습니다.</small>
          )}
        </div>

        {documentListItems.length > 0 && (
          <div className="document-list-controls">
            <label>
              <span>파일 찾기</span>
              <input
                type="search"
                value={documentQuery}
                placeholder="파일명 입력"
                onChange={(event) => {
                  setDocumentQuery(event.target.value);
                  setIsDocumentListExpanded(true);
                  setVisibleDocumentCount(DOCUMENT_PAGE_SIZE);
                }}
              />
            </label>
            <label>
              <span>상태</span>
              <select
                value={documentFilter}
                onChange={(event) => {
                  setDocumentFilter(event.target.value as DocumentListFilter);
                  setIsDocumentListExpanded(true);
                  setVisibleDocumentCount(DOCUMENT_PAGE_SIZE);
                }}
              >
                <option value="all">전체</option>
                <option value="selected">선택됨</option>
                <option value="active">RAG 가능</option>
                <option value="processing">처리 중</option>
                <option value="review_required">승인 대기</option>
                <option value="failed">실패</option>
              </select>
            </label>
          </div>
        )}

        {shouldShowDocumentDetails && (
          <div
            className="document-selection-list"
            id="registered-document-list"
            role="region"
            aria-label={`등록 문서 목록 ${filteredDocumentItems.length}개`}
          >
          {displayedDocumentItems.map((item) => {
            if (item.kind === "progress") {
              const { progress } = item;
              return (
                <article
                  className={`document-selection-card status-${progress.status}`}
                  key={item.key}
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
              );
            }

            if (item.kind === "legacy") {
              return (
                <span className="legacy-document-chip" key={item.key}>
                  <InterfaceIcon name="document" />
                  {item.filename}
                  <button type="button" aria-label={`${item.filename} 선택 해제`} onClick={() => onRemoveLegacyManual(item.index)}>×</button>
                </span>
              );
            }

            const currentDocument = item.document;
            const isSelected = selectedDocumentIdSet.has(currentDocument.document_id);
            const progress = progressByDocument.get(currentDocument.document_id);
            return (
              <article className={`document-selection-card status-${currentDocument.status} ${isSelected ? "selected" : ""}`} key={item.key}>
                <div className="document-selection-heading">
                  <InterfaceIcon name="document" />
                  <span><strong>{currentDocument.original_filename}</strong><small>버전 {currentDocument.version_number}</small></span>
                  <span className={`document-status status-${currentDocument.status}`}>{documentAvailability(currentDocument)}</span>
                </div>
                <div className="document-selection-meta">
                  <span>{DOCUMENT_STATUS_LABELS[currentDocument.status]}</span>
                  {currentDocument.fallback_used && <span className="fallback-label">PyMuPDF 대체 처리</span>}
                </div>
                {currentDocument.processing_warning && <p className="document-chip-warning">{currentDocument.processing_warning}</p>}
                {currentDocument.failure_reason && <p className="document-chip-warning" role="alert">{currentDocument.failure_reason}</p>}
                {progress && <ProgressDetails progress={progress} />}
                <div className="document-selection-actions">
                  <button type="button" className={isSelected ? "document-select-button selected" : "document-select-button"} aria-pressed={isSelected} onClick={() => onToggleDocument(currentDocument.document_id)}>
                    {isSelected ? "검색 범위에 포함됨" : "검색 범위에 추가"}
                  </button>
                  {(onReindexDocument || onDeleteDocument) && (
                    <details className="document-manage-menu">
                      <summary aria-label={`${currentDocument.original_filename} 관리 메뉴`}>관리</summary>
                      <div>
                        {onReindexDocument && (
                          <button type="button" className="document-reindex-button" onClick={() => onReindexDocument(currentDocument.document_id, currentDocument.original_filename)}>
                            비전 재인덱싱
                          </button>
                        )}
                        {onDeleteDocument && (
                          <button
                            type="button"
                            className="document-delete-button"
                            onClick={() => setConfirmingDelete({ documentId: currentDocument.document_id, filename: currentDocument.original_filename })}
                          >
                            삭제
                          </button>
                        )}
                      </div>
                    </details>
                  )}
                </div>
              </article>
            );
          })}

          {documentListItems.length === 0 && (
            <div className="resource-empty"><InterfaceIcon name="document" /><strong>선택된 문서가 없습니다.</strong><span>매뉴얼 없이도 공용 안전자료로 질문할 수 있습니다.</span></div>
          )}
          {documentListItems.length > 0 && filteredDocumentItems.length === 0 && (
            <div className="resource-empty"><InterfaceIcon name="document" /><strong>조건에 맞는 문서가 없습니다.</strong><span>파일명이나 상태 조건을 변경해 주세요.</span></div>
          )}
          </div>
        )}

        {isDocumentListExpanded && hasMoreDocuments && (
          <button
            type="button"
            className="document-load-more"
            onClick={() => setVisibleDocumentCount((current) => current + DOCUMENT_PAGE_SIZE)}
          >
            ＋ {Math.min(DOCUMENT_PAGE_SIZE, filteredDocumentItems.length - visibleDocumentCount)}개 더 보기
          </button>
        )}
      </section>

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
