"use client";

import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import ChatSources from "@/components/ChatSources";
import type {
  ChatSource,
  ComponentAnswerDetails,
  DocumentAnswerDetails,
  EvidenceBackedItem,
  MaintenanceAnswerDetails,
} from "@/types/chat";


type TabbedAnswer =
  | MaintenanceAnswerDetails
  | ComponentAnswerDetails
  | DocumentAnswerDetails;

type Props = {
  answerText: string;
  structuredAnswer: TabbedAnswer;
  sources: ChatSource[];
  warning?: string | null;
  onOpenDocument: (source: ChatSource) => void;
};

type TabDefinition = {
  id: string;
  label: string;
  content: ReactNode;
};

function sourcePageLabel(source: ChatSource): string {
  if (source.page_start) {
    const end = source.page_end;
    return end && end !== source.page_start
      ? `${source.page_start}–${end}페이지`
      : `${source.page_start}페이지`;
  }
  return source.page ? `${source.page}페이지` : "페이지 정보 없음";
}

function EvidenceList({
  items,
  sourceNumbers,
  sourceAnchorId,
  onSelectSource,
  emptyMessage = "확인된 내용이 없습니다.",
}: {
  items: EvidenceBackedItem[];
  sourceNumbers: ReadonlyMap<string, number>;
  sourceAnchorId: (number: number) => string;
  onSelectSource: (number: number) => void;
  emptyMessage?: string;
}) {
  if (!items.length) {
    return <p className="answer-tab-empty">{emptyMessage}</p>;
  }

  return (
    <ul className="answer-tab-list">
      {items.map((item, index) => {
        const numbers = item.evidence_chunk_ids
          .map((id) => sourceNumbers.get(id))
          .filter((value): value is number => value !== undefined);
        return (
          <li key={`${item.content}-${index}`}>
            <span>{item.content}</span>
            {numbers.length > 0 && (
              <small className="answer-tab-evidence-links">
                근거{" "}
                {numbers.map((number) => (
                  <a
                    href={`#${sourceAnchorId(number)}`}
                    key={number}
                    onClick={(event) => {
                      event.preventDefault();
                      onSelectSource(number);
                    }}
                  >
                    [{number}]
                  </a>
                ))}
              </small>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function TextList({
  items,
  emptyMessage = "확인된 내용이 없습니다.",
}: {
  items: string[];
  emptyMessage?: string;
}) {
  if (!items.length) {
    return <p className="answer-tab-empty">{emptyMessage}</p>;
  }
  return (
    <ul className="answer-tab-list">
      {items.map((item) => <li key={item}>{item}</li>)}
    </ul>
  );
}

function AnswerSection({
  title,
  children,
  tone = "default",
}: {
  title: string;
  children: ReactNode;
  tone?: "default" | "warning" | "danger" | "muted";
}) {
  return (
    <section className={`answer-tab-section tone-${tone}`}>
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function NaturalAnswer({ answer }: { answer: string }) {
  return (
    <section className="answer-tab-natural" aria-label="AI 핵심 답변">
      <div className="answer-tab-natural-heading">
        <strong>AI 핵심 답변</strong>
        <span>검색 근거를 바탕으로 요약한 답변</span>
      </div>
      <p>{answer}</p>
    </section>
  );
}

function SourceReferenceList({
  sources,
  sourceNumbers,
  sourceAnchorId,
  onSelectSource,
}: {
  sources: ChatSource[];
  sourceNumbers: ReadonlyMap<string, number>;
  sourceAnchorId: (number: number) => string;
  onSelectSource: (number: number) => void;
}) {
  if (!sources.length) {
    return <p className="answer-tab-empty">해당 유형의 출처가 없습니다.</p>;
  }
  return (
    <ul className="answer-source-reference-list">
      {sources.map((source) => {
        const number = sourceNumbers.get(source.chunk_id);
        if (!number) return null;
        return (
          <li key={source.chunk_id}>
            <a
              href={`#${sourceAnchorId(number)}`}
              onClick={(event) => {
                event.preventDefault();
                onSelectSource(number);
              }}
            >
              근거 {number} · {source.original_filename || source.title}
            </a>
            <span>
              {[sourcePageLabel(source), source.section ? `섹션: ${source.section}` : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function evidenceFromSourceTypes(
  items: EvidenceBackedItem[],
  sourcesById: ReadonlyMap<string, ChatSource>,
  sourceTypes: ReadonlySet<string>,
): EvidenceBackedItem[] {
  return items.filter((item) => item.evidence_chunk_ids.some((id) => {
    const source = sourcesById.get(id);
    return source ? sourceTypes.has(source.source_type) : false;
  }));
}

export default function AnswerTabs({
  answerText,
  structuredAnswer,
  sources,
  warning,
  onOpenDocument,
}: Props) {
  const rawInstanceId = useId();
  const instanceId = `answer-${rawInstanceId.replaceAll(":", "")}`;
  const sourceNumbers = useMemo(
    () => new Map(sources.map((source, index) => [source.chunk_id, index + 1])),
    [sources],
  );
  const sourcesById = useMemo(
    () => new Map(sources.map((source) => [source.chunk_id, source])),
    [sources],
  );
  const sourceAnchorId = (number: number) => `${instanceId}-chat-source-${number}`;
  const evidenceTabId = structuredAnswer.answer_type === "document_qa"
    ? "sources"
    : "evidence";

  const [activeTabId, setActiveTabId] = useState("summary");
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());

  const selectSource = (number: number) => {
    setActiveTabId(evidenceTabId);
    window.requestAnimationFrame(() => {
      const card = document.getElementById(sourceAnchorId(number));
      const details = card?.closest("details");
      if (details instanceof HTMLDetailsElement) details.open = true;
      card?.scrollIntoView?.({ block: "nearest" });
    });
  };

  const evidenceList = (
    items: EvidenceBackedItem[],
    emptyMessage?: string,
  ) => (
    <EvidenceList
      items={items}
      sourceNumbers={sourceNumbers}
      sourceAnchorId={sourceAnchorId}
      onSelectSource={selectSource}
      emptyMessage={emptyMessage}
    />
  );

  const sourceCards = (
    <ChatSources
      sources={sources}
      onOpenDocument={onOpenDocument}
      idPrefix={instanceId}
    />
  );

  let tabs: TabDefinition[];

  if (structuredAnswer.answer_type === "maintenance_guide") {
    const related = structuredAnswer.related_regulations_and_incidents;
    const lawItems = evidenceFromSourceTypes(
      related,
      sourcesById,
      new Set(["public_law"]),
    );
    const incidentItems = evidenceFromSourceTypes(
      related,
      sourcesById,
      new Set(["public_incident"]),
    );
    const guideItems = evidenceFromSourceTypes(
      related,
      sourcesById,
      new Set(["public_guide", "company_policy", "public_media"]),
    );
    const manualSources = sources.filter((source) => (
      source.source_type === "equipment_manual"
      || source.source_type === "component_manual"
    ));

    tabs = [
      {
        id: "summary",
        label: "요약",
        content: (
          <div className="answer-tab-stack">
            <NaturalAnswer answer={answerText} />
            <section className="answer-summary-grid">
              <div>
                <span>안전관리자 확인</span>
                <strong>{structuredAnswer.summary.status}</strong>
              </div>
            </section>
            <AnswerSection title="핵심 경고" tone="warning">
              <p>{structuredAnswer.summary.core_warning}</p>
            </AnswerSection>
            {structuredAnswer.summary.risk_basis.length > 0 && (
              <AnswerSection title="위험 판단 근거">
                {evidenceList(structuredAnswer.summary.risk_basis)}
              </AnswerSection>
            )}
            {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
          </div>
        ),
      },
      {
        id: "work-guide",
        label: "작업 안내",
        content: (
          <div className="answer-tab-stack">
            <AnswerSection title="작업 전 필수 확인사항">
              {evidenceList(structuredAnswer.pre_checks)}
            </AnswerSection>
            <AnswerSection title="주요 위험요인" tone="warning">
              {evidenceList(structuredAnswer.hazards.map(({ name, ...item }) => ({
                ...item,
                content: `${name}: ${item.content}`,
              })))}
            </AnswerSection>
            <AnswerSection title="매뉴얼 기반 작업 절차">
              {evidenceList(
                structuredAnswer.manual_steps,
                "매뉴얼에서 확인된 작업 절차가 없습니다.",
              )}
            </AnswerSection>
          </div>
        ),
      },
      {
        id: "safety-stop",
        label: "안전·중지",
        content: (
          <div className="answer-tab-stack">
            <AnswerSection title="작업 시 주의사항" tone="warning">
              {evidenceList(
                structuredAnswer.precautions,
                "확인된 주의사항이 없습니다.",
              )}
            </AnswerSection>
            <AnswerSection title="즉시 작업을 중지해야 하는 조건" tone="danger">
              {evidenceList(structuredAnswer.stop_conditions)}
            </AnswerSection>
            <AnswerSection title="추가 확인이 필요한 내용" tone="muted">
              <TextList items={structuredAnswer.additional_information_needed} />
            </AnswerSection>
            {structuredAnswer.conflicts.length > 0 && (
              <AnswerSection title="근거 간 차이" tone="warning">
                {evidenceList(structuredAnswer.conflicts)}
              </AnswerSection>
            )}
            {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
            <p className="answer-tab-critical">
              이 안내는 작업 승인이 아닙니다. 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.
            </p>
          </div>
        ),
      },
      {
        id: "evidence",
        label: "근거",
        content: (
          <div className="answer-tab-stack">
            <AnswerSection title="관련 법령">
              {evidenceList(lawItems)}
            </AnswerSection>
            <AnswerSection title="안전 가이드·회사 기준">
              {evidenceList(guideItems)}
            </AnswerSection>
            <AnswerSection title="사고사례">
              {evidenceList(incidentItems)}
            </AnswerSection>
            <AnswerSection title="매뉴얼 근거">
              <SourceReferenceList
                sources={manualSources}
                sourceNumbers={sourceNumbers}
                sourceAnchorId={sourceAnchorId}
                onSelectSource={selectSource}
              />
            </AnswerSection>
            {sources.length > 0
              ? sourceCards
              : <p className="answer-tab-empty">표시할 검색 출처가 없습니다.</p>}
          </div>
        ),
      },
    ];
  } else if (structuredAnswer.answer_type === "component_info") {
    tabs = [
      {
        id: "summary",
        label: "주요 역할",
        content: (
          <div className="answer-tab-stack">
            <NaturalAnswer answer={answerText} />
            <p className="answer-component-description">
              {structuredAnswer.one_line_description}
            </p>
            <AnswerSection title="주요 역할">
              {evidenceList(structuredAnswer.main_roles)}
            </AnswerSection>
            {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
          </div>
        ),
      },
      {
        id: "usage",
        label: "사용 위치",
        content: (
          <AnswerSection title="주로 사용하는 위치">
            {evidenceList(structuredAnswer.usage_locations)}
          </AnswerSection>
        ),
      },
      {
        id: "precautions",
        label: "주의사항",
        content: (
          <div className="answer-tab-stack">
            <AnswerSection title="사용 시 주의사항" tone="warning">
              {evidenceList(structuredAnswer.precautions)}
            </AnswerSection>
            <AnswerSection title="추가 확인이 필요한 내용" tone="muted">
              <TextList items={structuredAnswer.additional_information_needed} />
            </AnswerSection>
          </div>
        ),
      },
      {
        id: "evidence",
        label: "근거",
        content: (
          <div className="answer-tab-stack">
            {structuredAnswer.conflicts.length > 0 && (
              <AnswerSection title="근거 간 차이" tone="warning">
                {evidenceList(structuredAnswer.conflicts)}
              </AnswerSection>
            )}
            {sources.length > 0
              ? sourceCards
              : <p className="answer-tab-empty">표시할 검색 출처가 없습니다.</p>}
            {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
          </div>
        ),
      },
    ];
  } else {
    const overview = [
      ["파일명", structuredAnswer.overview.filename],
      ["문서 종류", structuredAnswer.overview.document_type],
      ["제조사", structuredAnswer.overview.manufacturer],
      ["모델명", structuredAnswer.overview.model_name],
      ["버전", structuredAnswer.overview.version],
      ["작성일", structuredAnswer.overview.authored_at],
    ].filter((item): item is [string, string] => Boolean(item[1]));

    tabs = [
      {
        id: "summary",
        label: "문서 개요",
        content: (
          <div className="answer-tab-stack">
            <NaturalAnswer answer={answerText} />
            <section className="answer-document-overview">
              <h4>문서 개요</h4>
              {overview.length > 0 ? (
                <dl>
                  {overview.map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : <p className="answer-tab-empty">확인된 문서 메타데이터가 없습니다.</p>}
            </section>
            {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
          </div>
        ),
      },
      {
        id: "related",
        label: "관련 항목",
        content: (
          <div className="answer-tab-stack">
            <AnswerSection title="관련 장비·부품">
              <TextList items={[
                ...structuredAnswer.related_equipment,
                ...structuredAnswer.related_components,
              ]} />
            </AnswerSection>
            <AnswerSection title="문서에서 확인 가능한 작업">
              <TextList items={structuredAnswer.supported_tasks} />
            </AnswerSection>
            <AnswerSection title="확인하지 못한 내용" tone="muted">
              <TextList items={structuredAnswer.unverified_information} />
            </AnswerSection>
            {structuredAnswer.conflicts.length > 0 && (
              <AnswerSection title="근거 간 차이" tone="warning">
                {evidenceList(structuredAnswer.conflicts)}
              </AnswerSection>
            )}
          </div>
        ),
      },
      {
        id: "sources",
        label: "출처",
        content: (
          <div className="answer-tab-stack">
            {sources.length > 0
              ? sourceCards
              : <p className="answer-tab-empty">표시할 문서 출처가 없습니다.</p>}
            {warning && <p className="answer-tab-warning">⚠ {warning}</p>}
          </div>
        ),
      },
    ];
  }

  useEffect(() => {
    setActiveTabId(tabs[0].id);
  }, [structuredAnswer]);

  const activeIndex = Math.max(
    0,
    tabs.findIndex((tab) => tab.id === activeTabId),
  );

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (index + 1) % tabs.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = tabs.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = tabs[nextIndex];
    setActiveTabId(nextTab.id);
    tabRefs.current.get(nextTab.id)?.focus();
  };

  return (
    <div className={`answer-tabs answer-tabs-${structuredAnswer.answer_type}`}>
      <div
        className="answer-tab-listbox"
        role="tablist"
        aria-label="AI 답변 상세 항목"
      >
        {tabs.map((tab, index) => {
          const selected = index === activeIndex;
          const tabId = `${instanceId}-tab-${tab.id}`;
          const panelId = `${instanceId}-panel-${tab.id}`;
          return (
            <button
              type="button"
              role="tab"
              id={tabId}
              aria-controls={panelId}
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              className={selected ? "selected" : ""}
              key={tab.id}
              ref={(element) => {
                if (element) tabRefs.current.set(tab.id, element);
                else tabRefs.current.delete(tab.id);
              }}
              onClick={() => setActiveTabId(tab.id)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {tabs.map((tab) => {
        const selected = tab.id === tabs[activeIndex].id;
        return (
          <section
            role="tabpanel"
            id={`${instanceId}-panel-${tab.id}`}
            aria-labelledby={`${instanceId}-tab-${tab.id}`}
            className="answer-tab-panel"
            hidden={!selected}
            key={tab.id}
            tabIndex={0}
          >
            {tab.content}
          </section>
        );
      })}
    </div>
  );
}
