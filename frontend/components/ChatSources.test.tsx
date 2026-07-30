import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ChatSources from "@/components/ChatSources";
import type { ChatSource } from "@/types/chat";

function source(index: number): ChatSource {
  return {
    document_id: `document-${index}`,
    document_version_id: `version-${index}`,
    chunk_id: `chunk-${index}`,
    title: `문서 ${index}`,
    source_type: "manual",
    document_scope: "company",
    original_filename: `manual-${index}.pdf`,
    document_version: 7,
    section: "설치",
    excerpt: `근거 내용 ${index}`,
    page: index,
    page_start: index,
    page_end: index,
    publisher: null,
    url: null,
    similarity: .8,
    keyword_score: .7,
    retrieval_score: .75,
    reranker_score: .78,
  };
}

describe("ChatSources", () => {
  it("처음 두 근거를 표시하고 나머지는 접이식 목록으로 제공한다", () => {
    render(<ChatSources sources={[source(1), source(2), source(3)]} onOpenDocument={() => {}} />);

    expect(screen.getByText("manual-1.pdf")).toBeInTheDocument();
    expect(screen.getByText("manual-2.pdf")).toBeInTheDocument();
    const summary = screen.getByText("나머지 근거 1건 보기");
    expect(summary).toBeInTheDocument();
    fireEvent.click(summary);
    expect(screen.getByText("manual-3.pdf")).toBeInTheDocument();
  });
});
