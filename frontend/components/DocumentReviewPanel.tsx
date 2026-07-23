"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ApproveDocumentResponse, ReviewQueueDocument } from "@/types/documents";
import { DOCUMENT_STATUS_LABELS } from "@/types/documents";

type Props = {
  apiBaseUrl: string;
  token: string;
  onApproved?: (approved: ApproveDocumentResponse) => void | Promise<void>;
  pollingIntervalMs?: number;
};

type AccessState = "checking" | "allowed" | "forbidden" | "unauthorized";

async function responseDetail(response: Response): Promise<string | null> {
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  return payload?.detail ?? null;
}

async function approvalErrorMessage(response: Response): Promise<string> {
  const detail = await responseDetail(response);
  if (response.status === 401) return "로그인이 만료되었습니다. 다시 로그인해 주세요.";
  if (response.status === 403) return "문서를 승인할 권한이 없습니다.";
  if (response.status === 404) return "문서 또는 문서 버전을 찾을 수 없습니다.";
  if (response.status === 409) return detail || "이미 승인됐거나 승인 가능한 상태가 아닙니다.";
  if (response.status >= 500) return "서버 오류로 문서를 승인하지 못했습니다. 잠시 후 다시 시도해 주세요.";
  return detail || "문서를 승인하지 못했습니다.";
}

function processingLabel(document: ReviewQueueDocument): string {
  const extractor = document.extractor?.toLowerCase() ?? "";
  if (document.fallback_used || extractor.includes("pymupdf")) return "PyMuPDF 대체 처리";
  if (extractor.includes("docling")) return "Docling";
  if (document.version_status === "pending") return "처리 대기";
  if (document.version_status === "processing") return "처리 중";
  return "처리 방식 확인 중";
}

export default function DocumentReviewPanel({
  apiBaseUrl,
  token,
  onApproved,
  pollingIntervalMs = 8000,
}: Props) {
  const [documents, setDocuments] = useState<ReviewQueueDocument[]>([]);
  const [accessState, setAccessState] = useState<AccessState>("checking");
  const [isLoading, setIsLoading] = useState(false);
  const [approvingVersionId, setApprovingVersionId] = useState<string | null>(null);
  const [confirmingDocument, setConfirmingDocument] = useState<ReviewQueueDocument | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  const loadQueue = useCallback(async (silent = false) => {
    if (!token) {
      setAccessState("unauthorized");
      return;
    }
    if (!silent) setIsLoading(true);
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/documents/review-queue?include_processing=true&limit=100`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (response.status === 403) {
        setAccessState("forbidden");
        setDocuments([]);
        return;
      }
      if (response.status === 401) {
        setAccessState("unauthorized");
        setDocuments([]);
        setError("로그인이 만료되었습니다. 다시 로그인해 주세요.");
        return;
      }
      if (!response.ok) {
        throw new Error(await responseDetail(response) || "승인 대기 문서를 불러오지 못했습니다.");
      }
      const payload = await response.json() as ReviewQueueDocument[];
      setDocuments(payload);
      setAccessState("allowed");
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "승인 대기 문서를 불러오지 못했습니다.");
    } finally {
      if (!silent) setIsLoading(false);
    }
  }, [apiBaseUrl, token]);

  useEffect(() => {
    void loadQueue();
  }, [loadQueue]);

  const hasProcessingDocuments = useMemo(
    () => documents.some((document) => ["pending", "processing"].includes(document.version_status)),
    [documents],
  );

  useEffect(() => {
    if (accessState !== "allowed" || !hasProcessingDocuments) return;
    const timer = window.setInterval(() => void loadQueue(true), pollingIntervalMs);
    return () => window.clearInterval(timer);
  }, [accessState, hasProcessingDocuments, loadQueue, pollingIntervalMs]);

  useEffect(() => {
    if (!confirmingDocument) return;
    cancelButtonRef.current?.focus();
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !approvingVersionId) setConfirmingDocument(null);
    };
    window.addEventListener("keydown", closeWithEscape);
    return () => window.removeEventListener("keydown", closeWithEscape);
  }, [approvingVersionId, confirmingDocument]);

  async function approveDocument(document: ReviewQueueDocument) {
    if (approvingVersionId || document.version_status !== "review_required") return;
    setApprovingVersionId(document.document_version_id);
    setError("");
    setSuccess("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/documents/${document.document_id}/versions/${document.document_version_id}/approve`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        },
      );
      if (!response.ok) throw new Error(await approvalErrorMessage(response));
      const approved = await response.json() as ApproveDocumentResponse;
      setDocuments((current) => current.filter(
        (item) => item.document_version_id !== document.document_version_id,
      ));
      setConfirmingDocument(null);
      setSuccess(`${document.original_filename} 버전 ${document.version_number} 승인이 완료되었습니다. 일반 RAG 검색에 사용할 수 있습니다.`);
      await onApproved?.(approved);
      await loadQueue(true);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "문서를 승인하지 못했습니다.");
    } finally {
      setApprovingVersionId(null);
    }
  }

  if (accessState === "forbidden") return null;

  return (
    <section className="document-review-panel" aria-label="문서 승인 관리">
      <div className="document-review-heading">
        <div>
          <span className="answer-type-label">관리자 기능</span>
          <h2>문서 승인 관리</h2>
          <p>PDF 처리가 끝난 문서를 검토하고 일반 RAG 검색에 사용할 버전을 승인합니다.</p>
        </div>
        <button type="button" onClick={() => void loadQueue()} disabled={isLoading || accessState === "checking"}>
          {isLoading ? "불러오는 중..." : "새로고침"}
        </button>
      </div>

      {accessState === "checking" && <p className="document-review-empty">승인 권한을 확인하고 있습니다.</p>}
      {accessState === "unauthorized" && <p className="document-review-error" role="alert">로그인이 만료되었습니다. 다시 로그인해 주세요.</p>}
      {success && <p className="document-review-success" role="status">{success}</p>}
      {error && accessState !== "unauthorized" && <p className="document-review-error" role="alert">{error}</p>}

      {accessState === "allowed" && documents.length === 0 && (
        <p className="document-review-empty">현재 승인 대기 또는 처리 중인 문서가 없습니다.</p>
      )}

      {accessState === "allowed" && documents.length > 0 && (
        <div className="document-review-list">
          {documents.map((document) => {
            const isApproving = approvingVersionId === document.document_version_id;
            const canApprove = document.version_status === "review_required";
            return (
              <article className={`document-review-card status-${document.version_status}`} key={document.document_version_id}>
                <div className="document-review-card-heading">
                  <div>
                    <strong>{document.original_filename}</strong>
                    <span>버전 {document.version_number} · {document.uploader_name || "업로더 정보 없음"}</span>
                  </div>
                  <span className={`document-status status-${document.version_status}`}>
                    {DOCUMENT_STATUS_LABELS[document.version_status]}
                  </span>
                </div>
                <dl className="document-review-metadata">
                  <div><dt>처리 방식</dt><dd>{processingLabel(document)}</dd></div>
                  <div><dt>페이지</dt><dd>{document.page_count ?? "확인 중"}</dd></div>
                  <div><dt>문서 유형</dt><dd>{document.document_type_code}</dd></div>
                  <div><dt>접근 등급</dt><dd>{document.access_level}</dd></div>
                </dl>
                {document.processing_warning && <p className="document-processing-warning">⚠ {document.processing_warning}</p>}
                {document.failure_reason && <p className="document-review-error">{document.failure_reason}</p>}
                {(canApprove || ["pending", "processing"].includes(document.version_status)) && (
                  <button
                    className="document-approve-button"
                    type="button"
                    disabled={!canApprove || isApproving}
                    onClick={() => setConfirmingDocument(document)}
                  >
                    {isApproving ? "승인 처리 중..." : canApprove ? "문서 버전 승인" : DOCUMENT_STATUS_LABELS[document.version_status]}
                  </button>
                )}
              </article>
            );
          })}
        </div>
      )}

      {confirmingDocument && (
        <div className="document-confirm-backdrop" role="presentation">
          <div className="document-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="document-approve-title" aria-describedby="document-approve-description">
            <h3 id="document-approve-title">문서 승인 확인</h3>
            <p id="document-approve-description">
              <strong>{confirmingDocument.original_filename}</strong>의 버전 {confirmingDocument.version_number}을 승인하시겠습니까?
              승인하면 일반 RAG 검색에서 사용할 수 있습니다.
            </p>
            <div>
              <button ref={cancelButtonRef} type="button" onClick={() => setConfirmingDocument(null)} disabled={Boolean(approvingVersionId)}>취소</button>
              <button
                className="confirm-approve"
                type="button"
                disabled={Boolean(approvingVersionId)}
                onClick={() => void approveDocument(confirmingDocument)}
              >
                {approvingVersionId ? "승인 처리 중..." : "승인하기"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
