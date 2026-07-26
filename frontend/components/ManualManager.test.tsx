import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ManualManager from "@/components/ManualManager";
import type { FileProcessingProgress, UserDocumentSummary } from "@/types/documents";

const document: UserDocumentSummary = {
  document_id: "document-1",
  document_version_id: "version-1",
  original_filename: "라이트커튼.pdf",
  title: "라이트커튼",
  version_number: 7,
  document_type_code: "manual",
  source_type: "manual",
  access_level: "company",
  lifecycle_status: "review_required",
  status: "review_required",
  is_active: false,
  extractor: "docling",
  fallback_used: false,
  processing_warning: null,
  failure_reason: null,
  page_count: 10,
  created_at: "2026-07-23T00:00:00Z",
};

describe("ManualManager", () => {
  it("문서 상태와 검색 범위 선택 동작을 구분해 표시한다", () => {
    const onToggleDocument = vi.fn();
    render(
      <ManualManager
        manuals={[document.original_filename]}
        documents={[document]}
        selectedDocumentIds={[]}
        manualStatus="PDF 처리 완료"
        sitePhotoName=""
        visionStatus=""
        isVisionLoading={false}
        onAddManuals={vi.fn()}
        onAddPhoto={vi.fn()}
        onToggleDocument={onToggleDocument}
        onRemoveLegacyManual={vi.fn()}
      />,
    );

    expect(screen.getByText("관리자 승인 필요")).toBeInTheDocument();
    expect(screen.getByText("승인 대기")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "검색 범위에 추가" }));
    expect(onToggleDocument).toHaveBeenCalledWith("document-1");
  });

  it("문서가 없을 때 공용 안전자료 질문 안내를 표시한다", () => {
    render(
      <ManualManager
        manuals={[]}
        documents={[]}
        selectedDocumentIds={[]}
        manualStatus=""
        sitePhotoName=""
        visionStatus=""
        isVisionLoading={false}
        onAddManuals={vi.fn()}
        onAddPhoto={vi.fn()}
        onToggleDocument={vi.fn()}
        onRemoveLegacyManual={vi.fn()}
      />,
    );

    expect(screen.getByText("선택된 문서가 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("매뉴얼 없이도 공용 안전자료로 질문할 수 있습니다.")).toBeInTheDocument();
  });

  it("실제 worker 진행률과 처리 개수를 파일별로 표시한다", () => {
    const progress: FileProcessingProgress = {
      client_key: document.document_id,
      document_id: document.document_id,
      document_version_id: document.document_version_id,
      filename: document.original_filename,
      status: "processing",
      stage: "embedding",
      attempt: 1,
      progress_percent: 74,
      message: "문서 청크 임베딩 생성 중 · 480/700",
      processed_pages: 520,
      total_pages: 520,
      processed_chunks: 700,
      total_chunks: 700,
      embedded_chunks: 480,
      updated_at: "2026-07-24T12:00:00Z",
      is_terminal: false,
      rag_ready: false,
    };
    render(
      <ManualManager
        manuals={[document.original_filename]}
        documents={[{ ...document, status: "processing", lifecycle_status: "processing" }]}
        processingProgress={[progress]}
        selectedDocumentIds={[]}
        manualStatus="PDF 처리 중"
        sitePhotoName=""
        visionStatus=""
        isVisionLoading={false}
        onAddManuals={vi.fn()}
        onAddPhoto={vi.fn()}
        onToggleDocument={vi.fn()}
        onRemoveLegacyManual={vi.fn()}
      />,
    );

    const progressbar = screen.getByRole("progressbar", {
      name: "라이트커튼.pdf 처리 진행률",
    });
    expect(progressbar).toHaveAttribute("value", "74");
    expect(screen.getByText("페이지 520/520 · 청크 700/700 · 임베딩 480/700")).toBeInTheDocument();
  });
});
