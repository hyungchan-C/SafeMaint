"use client";

import type {
  ChatSource,
  StructuredAnswer,
} from "@/types/chat";

import SafetyAnswerView from "@/components/SafetyAnswerView";
import StructuredChatAnswer from "@/components/StructuredChatAnswer";


type Props = {
  answer: string;
  structuredAnswer?: StructuredAnswer | null;
  sources: ChatSource[];
};

export default function ChatAnswerContent({
  answer,
  structuredAnswer,
  sources,
}: Props) {
  return (
    <div className="chat-answer-content">
      <section className="chat-natural-answer" aria-label="AI 핵심 답변">
        <div className="chat-answer-section-heading">
          <strong>AI 핵심 답변</strong>
          <span>검증된 검색 근거를 바탕으로 생성된 요약</span>
        </div>
        <SafetyAnswerView answer={answer} />
      </section>
      {structuredAnswer && (
        <section className="chat-structured-details" aria-label="근거 기반 상세 내용">
          <div className="chat-answer-section-heading">
            <strong>근거 기반 상세 내용</strong>
            <span>항목별 근거 번호를 아래 출처 카드에서 확인하세요.</span>
          </div>
          <StructuredChatAnswer
            answer={structuredAnswer}
            sources={sources}
          />
        </section>
      )}
    </div>
  );
}
