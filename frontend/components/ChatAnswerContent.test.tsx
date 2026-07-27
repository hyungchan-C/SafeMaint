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
        sources={[source]}
        onOpenDocument={() => {}}
      />,
    );

    expect(screen.getByRole("tablist", { name: "AI 답변 상세 항목" })).toBeInTheDocument();
    expect(screen.getByText(/라이트커튼은 접근을 감지/)).toBeInTheDocument();
    expect(screen.getByText("위험 영역 접근 감지")).toBeInTheDocument();
  });

  it("keeps the natural answer when structured data is unavailable", () => {
    render(
      <ChatAnswerContent
        answer="검증 가능한 근거를 찾지 못했습니다."
        structuredAnswer={null}
        sources={[]}
        onOpenDocument={() => {}}
      />,
    );

    expect(screen.getByText("검증 가능한 근거를 찾지 못했습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("shows no-evidence guidance as a single card without empty tabs", () => {
    render(
      <ChatAnswerContent
        answer="검증 가능한 문서 근거가 필요합니다."
        structuredAnswer={{
          answer_type: "no_evidence",
          message: "질문과 일치하는 근거를 찾지 못했습니다.",
          required_information: ["정확한 모델명"],
          required_documents: ["승인된 제조사 매뉴얼"],
          work_safety_notice: "근거 확인 전에는 작업하지 마세요.",
        }}
        sources={[]}
        warning="검색 근거 없음"
        onOpenDocument={() => {}}
      />,
    );

    expect(screen.getByRole("region", { name: "AI 안내" })).toBeInTheDocument();
    expect(screen.getByText("정확한 모델명")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("shows clarification guidance as a single card", () => {
    render(
      <ChatAnswerContent
        answer="질문 목적을 확인해 주세요."
        structuredAnswer={{
          answer_type: "clarification_required",
          question: "부품 정보와 설치 방법 중 어떤 내용이 필요한가요?",
          options: ["부품 정보", "설치 방법"],
        }}
        sources={[]}
        onOpenDocument={() => {}}
      />,
    );

    expect(screen.getByText("부품 정보와 설치 방법 중 어떤 내용이 필요한가요?"))
      .toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });
});
