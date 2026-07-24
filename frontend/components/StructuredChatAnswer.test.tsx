import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SafetyAnswerView from "@/components/SafetyAnswerView";
import StructuredChatAnswer from "@/components/StructuredChatAnswer";
import type { ChatSource, StructuredAnswer } from "@/types/chat";


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
  excerpt: "검증된 매뉴얼 내용",
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

function renderAnswer(answer: StructuredAnswer) {
  return render(<StructuredChatAnswer answer={answer} checklistItems={[]} sources={[source]} />);
}

describe("StructuredChatAnswer", () => {
  it("renders document QA without risk judgment or TBM", () => {
    renderAnswer({
      answer_type: "document_qa",
      overview: {
        filename: "manual.pdf",
        document_type: "component_manual",
        manufacturer: null,
        model_name: null,
        version: "1",
        authored_at: null,
      },
      main_contents: [{ content: "문서 요약", evidence_chunk_ids: ["chunk-1"] }],
      related_equipment: [],
      related_components: ["라이트커튼"],
      supported_tasks: ["설치 기준 확인"],
      evidence_chunk_ids: ["chunk-1"],
      conflicts: [],
      unverified_information: [],
    });

    expect(screen.getByText("문서 개요")).toBeInTheDocument();
    expect(screen.queryByText("TBM 체크리스트")).not.toBeInTheDocument();
    expect(screen.queryByText("예상 위험도")).not.toBeInTheDocument();
  });

  it("renders component information without maintenance procedure", () => {
    renderAnswer({
      answer_type: "component_info",
      one_line_description: "광축 차단을 감지하는 장치입니다.",
      main_roles: [{ content: "접근 감지", evidence_chunk_ids: ["chunk-1"] }],
      usage_locations: [],
      precautions: [],
      evidence_chunk_ids: ["chunk-1"],
      conflicts: [],
      additional_information_needed: [],
    });

    expect(screen.getByText("광축 차단을 감지하는 장치입니다.")).toBeInTheDocument();
    expect(screen.queryByText("매뉴얼 기반 작업 절차")).not.toBeInTheDocument();
    expect(screen.queryByText("TBM 체크리스트")).not.toBeInTheDocument();
  });

  it("renders maintenance summary, stop conditions and real checkboxes", () => {
    render(
      <StructuredChatAnswer
        answer={{
          answer_type: "maintenance_guide",
          summary: {
            status: "안전관리자 확인 필요",
            risk_level: "높음",
            risk_basis: [{ content: "설치 매뉴얼 근거", evidence_chunk_ids: ["chunk-1"] }],
            core_warning: "모델별 기준을 확인하세요.",
          },
          pre_checks: [],
          hazards: [{ name: "오검출", content: "검출 성능 저하", evidence_chunk_ids: ["chunk-1"] }],
          manual_steps: [{ content: "설치 위치 확인", evidence_chunk_ids: ["chunk-1"] }],
          stop_conditions: [{ content: "모델 확인 불가", evidence_chunk_ids: ["chunk-1"] }],
          related_regulations_and_incidents: [],
          evidence_chunk_ids: ["chunk-1"],
          conflicts: [],
          additional_information_needed: [],
        }}
        checklistItems={[{
          id: null,
          content: "설치 위치 기준 확인",
          sequence: 1,
          is_required: true,
          is_completed: false,
          completed_by_user_id: null,
          completed_at: null,
          evidence_chunk_ids: ["chunk-1"],
        }]}
        sources={[source]}
      />,
    );

    expect(screen.getByText("위험성평가")).toBeInTheDocument();
    expect(screen.getByText("별도 위험성평가 필요")).toBeInTheDocument();
    expect(screen.getByText("4. 즉시 작업을 중지해야 하는 조건")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeInTheDocument();
  });

  it("renders no-evidence and clarification layouts", () => {
    const { rerender } = render(
      <StructuredChatAnswer
        answer={{
          answer_type: "no_evidence",
          message: "검증 근거가 없습니다.",
          required_information: ["모델명"],
          required_documents: ["제조사 매뉴얼"],
          work_safety_notice: null,
        }}
        checklistItems={[]}
        sources={[]}
      />,
    );
    expect(screen.getByText("검증 근거 부족")).toBeInTheDocument();

    rerender(
      <StructuredChatAnswer
        answer={{
          answer_type: "clarification_required",
          question: "어떤 정보가 필요한가요?",
          options: ["부품 정보", "설치 방법"],
        }}
        checklistItems={[]}
        sources={[]}
      />,
    );
    expect(screen.getByText("질문 목적 확인")).toBeInTheDocument();
  });

  it("keeps the legacy answer-only renderer", () => {
    render(<SafetyAnswerView answer="기존 문자열 답변" />);

    expect(screen.getByText("기존 문자열 답변")).toBeInTheDocument();
  });

  it("links structured evidence numbers to the matching source card", () => {
    render(
      <>
        <StructuredChatAnswer
          answer={{
            answer_type: "component_info",
            one_line_description: "검증 근거가 있는 부품 설명",
            main_roles: [
              { content: "접근 감지", evidence_chunk_ids: ["chunk-1"] },
            ],
            usage_locations: [],
            precautions: [],
            evidence_chunk_ids: ["chunk-1"],
            conflicts: [],
            additional_information_needed: [],
          }}
          checklistItems={[]}
          sources={[source]}
        />
        <article id="chat-source-1">검색 근거 카드</article>
      </>,
    );

    expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute(
      "href",
      "#chat-source-1",
    );
    expect(document.getElementById("chat-source-1")).not.toBeNull();
  });
});
