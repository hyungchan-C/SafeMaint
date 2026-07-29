import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationCenter from "@/components/NotificationCenter";
import type { ReviewQueueDocument } from "@/types/documents";

const reviewDocument: ReviewQueueDocument = {
  document_id: "document-1",
  document_version_id: "version-1",
  original_filename: "light-curtain.pdf",
  title: "라이트커튼 매뉴얼",
  version_number: 7,
  document_type_code: "equipment_manual",
  source_type: "manual",
  access_level: "restricted",
  lifecycle_status: "review_required",
  version_status: "review_required",
  is_active: false,
  uploaded_by_user_id: "uploader-1",
  uploader_name: "문서 업로더",
  created_at: "2026-07-29T01:00:00Z",
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("NotificationCenter", () => {
  it("shows the approval count and opens the review drawer", async () => {
    const fetchMock = vi.fn(async () => jsonResponse([reviewDocument]));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <NotificationCenter
        apiBaseUrl="http://localhost:8000"
        token="test-token"
        onUnauthorized={vi.fn()}
      />,
    );

    const button = await screen.findByRole("button", { name: "문서 승인 알림 1건" });
    expect(screen.getByText("1")).toBeInTheDocument();
    fireEvent.click(button);

    expect(screen.getByRole("dialog", { name: "문서 승인 알림" })).toBeInTheDocument();
    expect(await screen.findByText("light-curtain.pdf")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/documents/review-queue?include_processing=true&limit=100",
      { headers: { Authorization: "Bearer test-token" } },
    );
  });

  it("does not expose the notification button without approve permission", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "forbidden" }, 403)));

    render(
      <NotificationCenter
        apiBaseUrl="http://localhost:8000"
        token="worker-token"
        onUnauthorized={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "문서 승인 알림" })).not.toBeInTheDocument();
    });
  });

  it("returns focus to the bell when the drawer closes with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));

    render(
      <NotificationCenter
        apiBaseUrl="http://localhost:8000"
        token="test-token"
        onUnauthorized={vi.fn()}
      />,
    );

    const button = await screen.findByRole("button", { name: "문서 승인 알림" });
    fireEvent.click(button);
    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(button).toHaveFocus());
    expect(screen.queryByRole("dialog", { name: "문서 승인 알림" })).not.toBeInTheDocument();
  });
});
