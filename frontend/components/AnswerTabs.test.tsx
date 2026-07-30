import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AnswerTabs from "@/components/AnswerTabs";
import type {
  ChatSource,
  ComponentAnswerDetails,
  DocumentAnswerDetails,
  MaintenanceAnswerDetails,
} from "@/types/chat";


const manualSource: ChatSource = {
  document_id: "manual-doc",
  document_version_id: "manual-version",
  chunk_id: "manual-chunk",
  title: "라이트커튼 설치 매뉴얼",
  source_type: "component_manual",
  document_scope: "company",
  original_filename: "SFL-라이트커튼-설치매뉴얼.pdf",
  document_version: 7,
  section: "설치 전 확인",
  excerpt: "설치 전 안전거리와 광축 정렬 기준을 확인합니다.",
  page: 8,
  page_start: 7,
  page_end: 10,
  publisher: null,
  url: null,
  similarity: 0.91,
  keyword_score: 0.8,
  retrieval_score: 0.88,
  reranker_score: 0.9,
};

const lawSource: ChatSource = {
  ...manualSource,
  document_id: "law-doc",
  document_version_id: "law-version",
  chunk_id: "law-chunk",
  title: "산업안전보건 기준",
  source_type: "public_law",
  document_scope: "public",
  original_filename: "산업안전보건기준.pdf",
  section: "방호장치",
  excerpt: "위험구역에는 방호장치를 설치해야 합니다.",
  page: 12,
  page_start: 12,
  page_end: 12,
};

const incidentSource: ChatSource = {
  ...manualSource,
  document_id: "incident-doc",
  document_version_id: "incident-version",
  chunk_id: "incident-chunk",
  title: "프레스 방호장치 사고사례",
  source_type: "public_incident",
  document_scope: "public",
  original_filename: "프레스_사고사례.pdf",
  section: "사고 개요",
  excerpt: "방호장치 해제 상태에서 사고가 발생했습니다.",
  page: 2,
  page_start: 2,
  page_end: 2,
};

const maintenanceAnswer: MaintenanceAnswerDetails = {
  answer_type: "maintenance_guide",
  summary: {
    status: "안전관리자 확인 필요",
    risk_level: "판단 불가",
    risk_basis: [
      { content: "설치 기준 확인 필요", evidence_chunk_ids: ["manual-chunk"] },
    ],
    core_warning: "설치 전 제조사 기준을 확인하세요.",
  },
  pre_checks: [
    { content: "안전거리 기준 확인", evidence_chunk_ids: ["manual-chunk"] },
  ],
  hazards: [
    {
      name: "감지 실패",
      content: "광축 정렬이 맞지 않으면 감지되지 않을 수 있습니다.",
      evidence_chunk_ids: ["manual-chunk"],
    },
  ],
  manual_steps: [
    { content: "광축 정렬 상태를 확인합니다.", evidence_chunk_ids: ["manual-chunk"] },
  ],
  rating_performance_page_source_ids: ["manual-chunk"],
  precautions: [
    { content: "정렬 상태 유지", evidence_chunk_ids: ["manual-chunk"] },
  ],
  stop_conditions: [
    { content: "안전거리 기준을 확인하지 못한 경우", evidence_chunk_ids: ["law-chunk"] },
  ],
  related_regulations_and_incidents: [
    { content: "방호장치 설치 기준", evidence_chunk_ids: ["law-chunk"] },
    { content: "방호장치 해제 사고", evidence_chunk_ids: ["incident-chunk"] },
  ],
  evidence_chunk_ids: ["manual-chunk", "law-chunk", "incident-chunk"],
  conflicts: [],
  additional_information_needed: ["정확한 모델명"],
};

function renderTabs(
  structuredAnswer:
    | MaintenanceAnswerDetails
    | ComponentAnswerDetails
    | DocumentAnswerDetails,
  sources = [manualSource],
) {
  return render(
    <AnswerTabs
      answerText="질문에 대한 자연어 핵심 답변입니다."
      structuredAnswer={structuredAnswer}
      sources={sources}
      warning={null}
      onOpenDocument={() => {}}
    />,
  );
}

describe("AnswerTabs", () => {
  it("switches all maintenance guide tabs and supports arrow-key navigation", () => {
    renderTabs(maintenanceAnswer, [manualSource, lawSource, incidentSource]);

    const tablist = screen.getByRole("tablist", { name: "AI 답변 상세 항목" });
    const summaryTab = within(tablist).getByRole("tab", { name: "요약" });
    const workTab = within(tablist).getByRole("tab", { name: "작업 안내" });

    expect(summaryTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("질문에 대한 자연어 핵심 답변입니다.")).toBeInTheDocument();

    fireEvent.click(workTab);
    expect(workTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("안전거리 기준 확인")).toBeInTheDocument();
    expect(screen.getByText(/감지 실패: 광축 정렬/)).toBeInTheDocument();

    fireEvent.keyDown(workTab, { key: "ArrowRight" });
    expect(within(tablist).getByRole("tab", { name: "안전·중지" }))
      .toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("안전거리 기준을 확인하지 못한 경우")).toBeInTheDocument();
    expect(screen.getByText("추가 확인이 필요한 내용")).toBeInTheDocument();
  });

  it("renders component information as four purpose-specific tabs", () => {
    const answer: ComponentAnswerDetails = {
      answer_type: "component_info",
      one_line_description: "광축 차단을 감지하는 안전장치입니다.",
      main_roles: [
        { content: "위험구역 접근 감지", evidence_chunk_ids: ["manual-chunk"] },
      ],
      usage_locations: [
        { content: "프레스 위험구역", evidence_chunk_ids: ["manual-chunk"] },
      ],
      precautions: [
        { content: "설치 후 광축을 확인합니다.", evidence_chunk_ids: ["manual-chunk"] },
      ],
      evidence_chunk_ids: ["manual-chunk"],
      conflicts: [],
      additional_information_needed: ["제품 모델명"],
    };

    renderTabs(answer);

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "주요 역할",
      "사용 위치",
      "주의사항",
      "근거",
    ]);
    fireEvent.click(screen.getByRole("tab", { name: "사용 위치" }));
    expect(screen.getByText("프레스 위험구역")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "주의사항" }));
    expect(screen.getByText("설치 후 광축을 확인합니다.")).toBeInTheDocument();
  });

  it("renders document overview, related items, and source metadata", () => {
    const answer: DocumentAnswerDetails = {
      answer_type: "document_qa",
      overview: {
        filename: "SFL-라이트커튼-설치매뉴얼.pdf",
        document_type: "component_manual",
        manufacturer: "Safe Factory",
        model_name: "SFL-A",
        version: "7",
        authored_at: null,
      },
      main_contents: [
        { content: "설치 기준을 설명합니다.", evidence_chunk_ids: ["manual-chunk"] },
      ],
      related_equipment: ["프레스"],
      related_components: ["라이트커튼"],
      supported_tasks: ["설치", "점검"],
      evidence_chunk_ids: ["manual-chunk"],
      conflicts: [],
      unverified_information: ["체결 토크"],
    };

    renderTabs(answer);

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "문서 개요",
      "PDF 확인",
      "출처",
    ]);
    expect(screen.getByText("Safe Factory")).toBeInTheDocument();
    expect(screen.queryByText("모델명")).not.toBeInTheDocument();
    expect(screen.queryByText("SFL-A")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "PDF 확인" }));
    const pdfCheckPanel = screen.getByRole("tabpanel");
    expect(within(pdfCheckPanel).getByText("SFL-라이트커튼-설치매뉴얼.pdf")).toBeInTheDocument();
    expect(within(pdfCheckPanel).getByRole("button", { name: "원문 전체 보기" }))
      .toBeInTheDocument();
    expect(within(pdfCheckPanel).queryByText("라이트커튼")).not.toBeInTheDocument();
    expect(within(pdfCheckPanel).queryByText("설치")).not.toBeInTheDocument();
    expect(screen.getByText("확인하지 못한 내용")).toBeInTheDocument();
    expect(screen.getByText("체결 토크")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "출처" }));
    const sourcePanel = screen.getByRole("tabpanel");
    expect(within(sourcePanel).getByText("SFL-라이트커튼-설치매뉴얼.pdf"))
      .toBeInTheDocument();
    expect(within(sourcePanel).getByText(/7–10페이지/)).toBeInTheDocument();
    expect(within(sourcePanel).getByText(/섹션: 설치 전 확인/)).toBeInTheDocument();
  });

  it("moves to the evidence tab when an evidence number is selected", () => {
    renderTabs(maintenanceAnswer, [manualSource, lawSource, incidentSource]);

    fireEvent.click(screen.getByRole("link", { name: "[1]" }));

    expect(screen.getByRole("tab", { name: "근거" }))
      .toHaveAttribute("aria-selected", "true");
    const evidencePanel = screen.getByRole("tabpanel");
    expect(within(evidencePanel).getByText("SFL-라이트커튼-설치매뉴얼.pdf")).toBeInTheDocument();
  });

  it("does not render empty grouped headings and retains returned sources", () => {
    renderTabs(
      {
        ...maintenanceAnswer,
        related_regulations_and_incidents: [],
        additional_information_needed: [],
      },
      [manualSource, lawSource, incidentSource],
    );

    fireEvent.click(screen.getByRole("tab", { name: "안전·중지" }));
    expect(screen.queryByText("추가 확인이 필요한 내용")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "근거" }));
    expect(screen.queryByText("관련 법령")).not.toBeInTheDocument();
    expect(screen.queryByText("안전 가이드·회사 기준")).not.toBeInTheDocument();
    expect(screen.queryByText("사고사례")).not.toBeInTheDocument();
    expect(screen.getByText("산업안전보건기준.pdf")).toBeInTheDocument();
    expect(screen.getByText("프레스_사고사례.pdf")).toBeInTheDocument();
  });
});
