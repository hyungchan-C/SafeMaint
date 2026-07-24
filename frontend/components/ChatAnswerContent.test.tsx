import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ChatAnswerContent from "@/components/ChatAnswerContent";
import type { ChatSource } from "@/types/chat";


const source: ChatSource = {
  document_id: "doc-1",
  document_version_id: "version-1",
  chunk_id: "chunk-1",
  title: "라이트커튼 매뉴얼",
  source_type: "component_manual",
  document_scope: "company",
  original_filename: "manual.pdf",
  document_version: 1,
  section: "제품 개요",
  excerpt: "라이트커튼은 위험 영역 접근을 감지합니다.",
  page: 4,
  page_start: 4,
  page_end: 4,
  publisher: null,
  url: null,
  similarity: 0.8,
  keyword_score: 0.5,
  retrieval_score: 0.7,
  reranker_score: 0.75,
};

describe("ChatAnswerContent", () => {
  it("shows the natural answer and structured details together", () => {
    render(
      <ChatAnswerContent
        answer="라이트커튼은 접근을 감지하는 안전장치입니다. [1]"
        structuredAnswer={{
          answer_type: "component_info",
          one_line_description: "광축 차단을 감지합니다.",
          main_roles: [
            { content: "위험 영역 접근 감지", evidence_chunk_ids: ["chunk-1"] },
          ],
          usage_locations: [],
          precautions: [],
          evidence_chunk_ids: ["chunk-1"],
          conflicts: [],
          additional_information_needed: [],
        }}
        checklistItems={[]}
        sources={[source]}
      />,
    );

    expect(screen.getByRole("region", { name: "AI 핵심 답변" })).toBeInTheDocument();
    expect(screen.getByText(/라이트커튼은 접근을 감지/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "근거 기반 상세 내용" })).toBeInTheDocument();
    expect(screen.getByText("위험 영역 접근 감지")).toBeInTheDocument();
  });

  it("keeps the natural answer when structured data is unavailable", () => {
    render(
      <ChatAnswerContent
        answer="검증 가능한 근거를 찾지 못했습니다."
        structuredAnswer={null}
        checklistItems={[]}
        sources={[]}
      />,
    );

    expect(screen.getByText("검증 가능한 근거를 찾지 못했습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "근거 기반 상세 내용" })).not.toBeInTheDocument();
  });
});
