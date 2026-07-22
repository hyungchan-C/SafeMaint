import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DocumentReviewPanel from "@/components/DocumentReviewPanel";
import type { ReviewQueueDocument } from "@/types/documents";

const reviewDocument: ReviewQueueDocument = {
  document_id: "74360eba-c31e-4029-b5e8-5df3e1be277f",
  document_version_id: "98d77900-729d-4e68-87ac-a40033c96847",
  original_filename: "light-curtain.pdf",
  title: "라이트커튼 매뉴얼",
  version_number: 7,
  document_type_code: "equipment_manual",
  source_type: "manual",
  access_level: "restricted",
  lifecycle_status: "review_required",
  version_status: "review_required",
  is_active: false,
  uploaded_by_user_id: "user-1",
  uploader_name: "문서 업로더",
  created_at: "2026-07-22T00:00:00Z",
  extractor: "docling",
  fallback_used: false,
  processing_warning: null,
  failure_reason: null,
  page_count: 24,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPanel(onApproved?: () => void | Promise<void>) {
  return render(
    <DocumentReviewPanel
      apiBaseUrl="http://localhost:8000"
      token="test-access-token"
      onApproved={onApproved}
      pollingIntervalMs={60_000}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("DocumentReviewPanel", () => {
  it("shows an approval button only for review-required documents", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([reviewDocument])));
    renderPanel();

    expect(await screen.findByText("light-curtain.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "문서 버전 승인" })).toBeEnabled();
    expect(screen.getByText("Docling")).toBeInTheDocument();
  });

  it("keeps processing documents disabled and labels fallback processing", async () => {
    const processingDocument: ReviewQueueDocument = {
      ...reviewDocument,
      lifecycle_status: "processing",
      version_status: "processing",
      extractor: "pymupdf",
      fallback_used: true,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([processingDocument])));
    renderPanel();

    expect(await screen.findByText("PyMuPDF 대체 처리")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PDF 처리 중" })).toBeDisabled();
  });

  it("polls only while a document is processing and reveals approval when processing finishes", async () => {
    const processingDocument: ReviewQueueDocument = {
      ...reviewDocument,
      lifecycle_status: "processing",
      version_status: "processing",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([processingDocument]))
      .mockResolvedValue(jsonResponse([reviewDocument]));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <DocumentReviewPanel
        apiBaseUrl="http://localhost:8000"
        token="test-access-token"
        pollingIntervalMs={10}
      />,
    );

    expect(await screen.findByRole("button", { name: "PDF 처리 중" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: "문서 버전 승인" })).toBeEnabled();
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("renders an active document as approved without an approval action", async () => {
    const activeDocument: ReviewQueueDocument = {
      ...reviewDocument,
      lifecycle_status: "active",
      version_status: "active",
      is_active: true,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([activeDocument])));
    renderPanel();

    expect(await screen.findByText("승인 완료 · RAG 검색 가능")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "문서 버전 승인" })).not.toBeInTheDocument();
  });

  it("does not expose the management UI when the server returns 403", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "forbidden" }, 403)));
    renderPanel();

    await waitFor(() => expect(screen.queryByLabelText("문서 승인 관리")).not.toBeInTheDocument());
  });

  it("confirms approval, sends authorization once, and refreshes the list", async () => {
    const onApproved = vi.fn();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([reviewDocument]))
      .mockResolvedValueOnce(jsonResponse({
        document_id: reviewDocument.document_id,
        document_version_id: reviewDocument.document_version_id,
        version_number: 7,
        status: "active",
        is_active: true,
      }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    renderPanel(onApproved);

    fireEvent.click(await screen.findByRole("button", { name: "문서 버전 승인" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("버전 7을 승인하시겠습니까?");
    const confirmButton = screen.getByRole("button", { name: "승인하기" });
    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);

    expect(await screen.findByText(/버전 7 승인이 완료되었습니다/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1]).toEqual([
      `http://localhost:8000/api/v1/documents/${reviewDocument.document_id}/versions/${reviewDocument.document_version_id}/approve`,
      {
        method: "POST",
        headers: { Authorization: "Bearer test-access-token" },
      },
    ]);
    expect(onApproved).toHaveBeenCalledOnce();
    expect(screen.queryByText("light-curtain.pdf")).not.toBeInTheDocument();
  });

  it("keeps the document pending and reports an approval failure", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([reviewDocument]))
      .mockResolvedValueOnce(jsonResponse({ detail: "검토 대기 상태의 문서 버전만 승인할 수 있습니다." }, 409));
    vi.stubGlobal("fetch", fetchMock);
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "문서 버전 승인" }));
    fireEvent.click(screen.getByRole("button", { name: "승인하기" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("검토 대기 상태의 문서 버전만 승인할 수 있습니다.");
    expect(screen.getAllByText("light-curtain.pdf")).toHaveLength(2);
    expect(screen.queryByText(/승인이 완료되었습니다/)).not.toBeInTheDocument();
  });
});
