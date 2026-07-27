"use client";

import { useMemo } from "react";

import ChatChecklist from "@/components/ChatChecklist";
import type {
  ChatChecklistItem,
  ChatSource,
  EvidenceBackedItem,
  StructuredAnswer,
} from "@/types/chat";


type Props = {
  answer: StructuredAnswer;
  checklistItems: ChatChecklistItem[];
  sources: ChatSource[];
  savedAssessmentId: string | null;
  isSavingChecklist: boolean;
  onSaveChecklist: (checkedIndices: number[]) => void;
};

function EvidenceList({
  items,
  sourceNumbers,
}: {
  items: EvidenceBackedItem[];
  sourceNumbers: ReadonlyMap<string, number>;
}) {
  if (!items.length) return <p className="structured-empty">확인된 내용이 없습니다.</p>;
  return (
    <ul className="structured-list">
      {items.map((item, index) => (
        <li key={`${item.content}-${index}`}>
          <span>{item.content}</span>
          {item.evidence_chunk_ids.length > 0 && (
            <small>
              근거 {item.evidence_chunk_ids
                .map((id) => sourceNumbers.get(id))
                .filter((value): value is number => value !== undefined)
                .map((value) => (
                  <a
                    href={`#chat-source-${value}`}
                    key={value}
                    onClick={() => {
                      const card = document.getElementById(`chat-source-${value}`);
                      const details = card?.closest("details");
                      if (details instanceof HTMLDetailsElement) details.open = true;
                    }}
                  >
                    [{value}]
                  </a>
                ))}
            </small>
          )}
        </li>
      ))}
    </ul>
  );
}

function TextList({ items }: { items: string[] }) {
  if (!items.length) return <p className="structured-empty">확인된 내용이 없습니다.</p>;
  return <ul className="structured-list">{items.map((item) => <li key={item}>{item}</li>)}</ul>;
}

function ConflictSection({
  items,
  sourceNumbers,
}: {
  items: EvidenceBackedItem[];
  sourceNumbers: ReadonlyMap<string, number>;
}) {
  if (!items.length) return null;
  return (
    <section className="structured-section conflict-section">
      <h4>근거 간 차이</h4>
      <EvidenceList items={items} sourceNumbers={sourceNumbers} />
    </section>
  );
}

export default function StructuredChatAnswer({
  answer,
  checklistItems,
  sources,
  savedAssessmentId,
  isSavingChecklist,
  onSaveChecklist,
}: Props) {
  const sourceNumbers = useMemo(
    () => new Map(sources.map((source, index) => [source.chunk_id, index + 1])),
    [sources],
  );

  if (answer.answer_type === "clarification_required") {
    return (
      <div className="structured-answer clarification-answer">
        <span className="answer-type-label">질문 목적 확인</span>
        <h3>{answer.question}</h3>
        <div className="clarification-options">{answer.options.map((option) => <span key={option}>{option}</span>)}</div>
      </div>
    );
  }

  if (answer.answer_type === "no_evidence") {
    return (
      <div className="structured-answer no-evidence-answer">
        <span className="answer-type-label">검증 근거 부족</span>
        <h3>{answer.message}</h3>
        <section className="structured-section"><h4>추가로 필요한 정보</h4><TextList items={answer.required_information} /></section>
        <section className="structured-section"><h4>필요한 문서</h4><TextList items={answer.required_documents} /></section>
        {answer.work_safety_notice && <p className="structured-critical">{answer.work_safety_notice}</p>}
      </div>
    );
  }

  if (answer.answer_type === "document_qa") {
    const overview = [
      ["파일명", answer.overview.filename],
      ["문서 종류", answer.overview.document_type],
      ["제조사", answer.overview.manufacturer],
      ["모델명", answer.overview.model_name],
      ["버전", answer.overview.version],
      ["작성일", answer.overview.authored_at],
    ].filter((item): item is [string, string] => Boolean(item[1]));
    return (
      <div className="structured-answer document-answer">
        <span className="answer-type-label">문서 내용 답변</span>
        <section className="document-overview"><h3>문서 개요</h3>{overview.length ? <dl>{overview.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : <p>확인된 문서 메타데이터가 없습니다.</p>}</section>
        <section className="structured-columns">
          <div><h4>관련 장비·부품</h4><TextList items={[...answer.related_equipment, ...answer.related_components]} /></div>
          <div><h4>문서에서 확인할 수 있는 작업</h4><TextList items={answer.supported_tasks} /></div>
        </section>
        <ConflictSection items={answer.conflicts} sourceNumbers={sourceNumbers} />
      </div>
    );
  }

  if (answer.answer_type === "component_info") {
    return (
      <div className="structured-answer component-answer">
        <span className="answer-type-label">부품 정보</span>
        <p className="component-one-line">{answer.one_line_description}</p>
        <section className="structured-section"><h4>주요 역할</h4><EvidenceList items={answer.main_roles} sourceNumbers={sourceNumbers} /></section>
        <section className="structured-section"><h4>주로 사용하는 곳</h4><EvidenceList items={answer.usage_locations} sourceNumbers={sourceNumbers} /></section>
        <section className="structured-section"><h4>사용 시 주의사항</h4><EvidenceList items={answer.precautions} sourceNumbers={sourceNumbers} /></section>
        <ConflictSection items={answer.conflicts} sourceNumbers={sourceNumbers} />
        {answer.additional_information_needed.length > 0 && (
          <section className="structured-section muted"><h4>추가 확인 필요</h4><TextList items={answer.additional_information_needed} /></section>
        )}
      </div>
    );
  }

  return (
    <div className="structured-answer maintenance-answer">
      <span className="answer-type-label">유지보수 작업 안내</span>
      <section className="maintenance-summary risk-판단-불가">
        <div><span>상태</span><strong>{answer.summary.status}</strong></div>
        <p>{answer.summary.core_warning}</p>
        {answer.summary.risk_basis.length > 0 && (
          <EvidenceList items={answer.summary.risk_basis} sourceNumbers={sourceNumbers} />
        )}
      </section>
      <section className="structured-section"><h4>1. 작업 전 필수 확인사항</h4><EvidenceList items={answer.pre_checks} sourceNumbers={sourceNumbers} /></section>
      <section className="structured-section"><h4>2. 주요 위험요인</h4><EvidenceList items={answer.hazards.map(({ name, ...item }) => ({ ...item, content: `${name}: ${item.content}` }))} sourceNumbers={sourceNumbers} /></section>
      <section className="structured-section"><h4>3. 매뉴얼 기반 작업 절차</h4><EvidenceList items={answer.manual_steps} sourceNumbers={sourceNumbers} /></section>
      <section className="structured-section stop-section"><h4>4. 즉시 작업을 중지해야 하는 조건</h4><EvidenceList items={answer.stop_conditions} sourceNumbers={sourceNumbers} /></section>
      {answer.related_regulations_and_incidents.length > 0 && (
        <section className="structured-section"><h4>5. 관련 회사 기준·법령·가이드·사고사례</h4><EvidenceList items={answer.related_regulations_and_incidents} sourceNumbers={sourceNumbers} /></section>
      )}
      <ConflictSection items={answer.conflicts} sourceNumbers={sourceNumbers} />
      <ChatChecklist
        items={checklistItems}
        savedAssessmentId={savedAssessmentId}
        isSaving={isSavingChecklist}
        onSave={onSaveChecklist}
      />
      {answer.additional_information_needed.length > 0 && (
        <section className="structured-section muted"><h4>6. 추가 확인이 필요한 내용</h4><TextList items={answer.additional_information_needed} /></section>
      )}
      <p className="structured-critical">이 안내는 작업 승인이 아닙니다. 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.</p>
    </div>
  );
}
