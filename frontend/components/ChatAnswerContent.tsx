"use client";

import type {
  ChatChecklistItem,
  ChatSource,
  StructuredAnswer,
} from "@/types/chat";

import AnswerTabs from "@/components/AnswerTabs";
import ChatSources from "@/components/ChatSources";
import StructuredChatAnswer from "@/components/StructuredChatAnswer";


type Props = {
  answer: string;
  structuredAnswer?: StructuredAnswer | null;
  sources: ChatSource[];
  warning?: string | null;
  checklistItems?: ChatChecklistItem[];
  savedAssessmentId?: string | null;
  isSavingChecklist?: boolean;
  onSaveChecklist?: (checkedIndices: number[]) => void;
  onOpenDocument: (source: ChatSource) => void;
};

export default function ChatAnswerContent({
  answer,
  structuredAnswer,
  sources,
  warning,
  checklistItems = [],
  savedAssessmentId = null,
  isSavingChecklist = false,
  onSaveChecklist = () => {},
  onOpenDocument,
}: Props) {
  if (!structuredAnswer) {
    return (
      <section className="chat-single-answer-card" aria-label="AI 답변">
        <div className="chat-answer-section-heading">
          <strong>AI 답변</strong>
          <span>구조화된 상세 정보 없이 원문 답변을 표시합니다.</span>
        </div>
        <p className="chat-answer-full-text">{answer}</p>
        <ChatSources sources={sources} onOpenDocument={onOpenDocument} />
        {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
      </section>
    );
  }

  if (
    structuredAnswer.answer_type === "clarification_required"
    || structuredAnswer.answer_type === "no_evidence"
  ) {
    return (
      <section className="chat-single-answer-card" aria-label="AI 안내">
        <p className="chat-answer-full-text">{answer}</p>
        <StructuredChatAnswer
          answer={structuredAnswer}
          sources={sources}
        />
        <ChatSources sources={sources} onOpenDocument={onOpenDocument} />
        {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
      </section>
    );
  }

  return (
    <div className="chat-answer-content">
      <AnswerTabs
        answerText={answer}
        structuredAnswer={structuredAnswer}
        sources={sources}
        warning={warning}
        onOpenDocument={onOpenDocument}
        checklistItems={checklistItems}
        savedAssessmentId={savedAssessmentId}
        isSavingChecklist={isSavingChecklist}
        onSaveChecklist={onSaveChecklist}
      />
    </div>
  );
}
