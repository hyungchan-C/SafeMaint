"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import DocumentReviewPanel from "@/components/DocumentReviewPanel";
import InterfaceIcon from "@/components/InterfaceIcon";
import type { ApproveDocumentResponse, ReviewQueueDocument } from "@/types/documents";

type AccessState = "checking" | "allowed" | "forbidden";

type Props = {
  apiBaseUrl: string;
  token: string;
  pollingIntervalMs?: number;
  onUnauthorized: () => void;
  onDocumentApproved?: (approved: ApproveDocumentResponse) => void | Promise<void>;
};

async function responseDetail(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  return payload?.detail || fallback;
}

function playNotificationTone() {
  try {
    const audioContext = new window.AudioContext();
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(880, audioContext.currentTime);
    gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.16, audioContext.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.32);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.34);
    oscillator.addEventListener("ended", () => void audioContext.close(), { once: true });
  } catch {
    // Browsers can block audio before the first user interaction. The badge and toast remain available.
  }
}

export default function NotificationCenter({
  apiBaseUrl,
  token,
  pollingIntervalMs = 10_000,
  onUnauthorized,
  onDocumentApproved,
}: Props) {
  const [accessState, setAccessState] = useState<AccessState>("checking");
  const [reviewDocuments, setReviewDocuments] = useState<ReviewQueueDocument[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [toastDocument, setToastDocument] = useState<ReviewQueueDocument | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const initializedRef = useRef(false);
  const previousReviewIdsRef = useRef<Set<string>>(new Set());
  const inFlightRef = useRef(false);

  const loadReviewQueue = useCallback(async (silent = false) => {
    if (!token || inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) setIsLoading(true);
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/documents/review-queue?include_processing=true&limit=100`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      if (response.status === 403) {
        setAccessState("forbidden");
        setReviewDocuments([]);
        return;
      }
      if (!response.ok) {
        throw new Error(await responseDetail(response, "문서 승인 알림을 불러오지 못했습니다."));
      }

      const queue = await response.json() as ReviewQueueDocument[];
      const reviewRequired = queue.filter((document) => document.version_status === "review_required");
      const currentReviewIds = new Set(
        reviewRequired.map((document) => document.document_version_id),
      );

      if (initializedRef.current) {
        const newReviewDocument = reviewRequired.find(
          (document) => !previousReviewIdsRef.current.has(document.document_version_id),
        );
        if (newReviewDocument) {
          setToastDocument(newReviewDocument);
          playNotificationTone();
        }
      }

      initializedRef.current = true;
      previousReviewIdsRef.current = currentReviewIds;
      setReviewDocuments(reviewRequired);
      setAccessState("allowed");
      setError("");
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "문서 승인 알림을 불러오지 못했습니다.",
      );
    } finally {
      inFlightRef.current = false;
      if (!silent) setIsLoading(false);
    }
  }, [apiBaseUrl, onUnauthorized, token]);

  useEffect(() => {
    void loadReviewQueue();
    const timer = window.setInterval(() => void loadReviewQueue(true), pollingIntervalMs);
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void loadReviewQueue(true);
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [loadReviewQueue, pollingIntervalMs]);

  useEffect(() => {
    if (!isOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setIsOpen(false);
      window.setTimeout(() => buttonRef.current?.focus(), 0);
    };
    window.addEventListener("keydown", closeWithEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeWithEscape);
    };
  }, [isOpen]);

  if (accessState === "forbidden") return null;

  const reviewCount = reviewDocuments.length;
  const badgeLabel = reviewCount > 99 ? "99+" : String(reviewCount);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="header-action notification-button"
        aria-label={reviewCount ? `문서 승인 알림 ${reviewCount}건` : "문서 승인 알림"}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        onClick={() => {
          setIsOpen(true);
          setToastDocument(null);
          void loadReviewQueue();
        }}
      >
        <InterfaceIcon name="bell" />
        <span>알림</span>
        {reviewCount > 0 && (
          <strong className="notification-badge" aria-live="polite">{badgeLabel}</strong>
        )}
      </button>

      {toastDocument && createPortal((
        <aside className="notification-toast" role="status" aria-live="polite">
          <strong>새 문서 승인 요청이 도착했습니다.</strong>
          <span>{toastDocument.original_filename} · 버전 {toastDocument.version_number}</span>
          <div>
            <button
              type="button"
              onClick={() => {
                setToastDocument(null);
                setIsOpen(true);
              }}
            >
              확인하기
            </button>
            <button type="button" onClick={() => setToastDocument(null)}>닫기</button>
          </div>
        </aside>
      ), document.body)}

      {isOpen && createPortal((
        <div
          className="notification-backdrop"
          role="presentation"
          onMouseDown={() => {
            setIsOpen(false);
            window.setTimeout(() => buttonRef.current?.focus(), 0);
          }}
        >
          <aside
            className="notification-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="notification-drawer-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <h2 id="notification-drawer-title">문서 승인 알림</h2>
                <span>PDF 처리가 끝난 문서를 검토하고 승인합니다.</span>
              </div>
              <button
                ref={closeButtonRef}
                type="button"
                onClick={() => {
                  setIsOpen(false);
                  window.setTimeout(() => buttonRef.current?.focus(), 0);
                }}
                aria-label="문서 승인 알림 닫기"
              >
                닫기
              </button>
            </header>
            <div className="notification-toolbar">
              <strong>승인 대기 {reviewCount > 99 ? "99+" : reviewCount}건</strong>
              <button type="button" onClick={() => void loadReviewQueue()} disabled={isLoading}>
                {isLoading ? "새로고침 중" : "새로고침"}
              </button>
            </div>
            {error && (
              <div className="notification-error" role="alert">
                <span>{error}</span>
                <button type="button" onClick={() => void loadReviewQueue()}>다시 시도</button>
              </div>
            )}
            <DocumentReviewPanel
              apiBaseUrl={apiBaseUrl}
              token={token}
              onApproved={async (approved) => {
                await loadReviewQueue(true);
                await onDocumentApproved?.(approved);
              }}
            />
          </aside>
        </div>
      ), document.body)}
    </>
  );
}
