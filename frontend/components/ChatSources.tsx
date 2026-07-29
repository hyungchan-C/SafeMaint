import type { ChatSource } from "@/types/chat";

const SOURCE_TYPE_LABELS: Record<string, string> = {
  equipment_manual: "설비 매뉴얼",
  component_manual: "부품 매뉴얼",
  company_policy: "회사 기준",
  public_law: "법령",
  public_guide: "안전 가이드",
  public_media: "안전자료",
  public_incident: "사고사례",
};

function sourceTypeLabel(sourceType: string) {
  return SOURCE_TYPE_LABELS[sourceType] ?? sourceType;
}

function SourceCard({
  source,
  number,
  onOpenDocument,
  idPrefix,
}: {
  source: ChatSource;
  number: number;
  onOpenDocument: (source: ChatSource) => void;
  idPrefix: string;
}) {
  const pageLabel = source.page_start
    ? `${source.page_start}${source.page_end && source.page_end !== source.page_start ? `–${source.page_end}` : ""}페이지`
    : source.page ? `${source.page}페이지` : "페이지 정보 없음";

  return (
    <article
      className="chat-source-card"
      id={`${idPrefix ? `${idPrefix}-` : ""}chat-source-${number}`}
    >
      <div className="chat-source-heading">
        <span className="source-number">근거 {number}</span>
        <span>{sourceTypeLabel(source.source_type)} · 유사도 {(source.similarity * 100).toFixed(1)}%</span>
      </div>
      <strong>{source.original_filename || source.title}</strong>
      <small className="chat-source-location">{[pageLabel, source.section ? `섹션: ${source.section}` : null, source.document_version ? `버전 ${source.document_version}` : null].filter(Boolean).join(" · ")}</small>
      <p>{source.excerpt}</p>
      <div className="source-card-footer">
        <button type="button" className="chat-source-open" onClick={() => onOpenDocument(source)}>문서 원문 보기</button>
        {source.url && <a href={source.url} target="_blank" rel="noreferrer">원문 확인</a>}
        <details className="source-technical"><summary>식별 정보</summary><small>문서 ID {source.document_id}{source.document_version_id ? ` · 버전 ID ${source.document_version_id}` : ""}</small></details>
      </div>
    </article>
  );
}

export default function ChatSources({
  sources,
  onOpenDocument,
  idPrefix = "",
  hideHeading = false,
}: {
  sources: ChatSource[];
  onOpenDocument: (source: ChatSource) => void;
  idPrefix?: string;
  hideHeading?: boolean;
}) {
  if (!sources.length) return null;
  const visible = sources.slice(0, 2);
  const hidden = sources.slice(2);

  return (
    <section className="chat-source-list" aria-label={`검색 근거 ${sources.length}건`}>
      {!hideHeading && (
        <div className="chat-source-list-heading"><strong>검색 근거</strong><span>{sources.length}건</span></div>
      )}
      {visible.map((source, index) => <SourceCard key={source.chunk_id} source={source} number={index + 1} onOpenDocument={onOpenDocument} idPrefix={idPrefix} />)}
      {hidden.length > 0 && (
        <details className="additional-sources">
          <summary>나머지 근거 {hidden.length}건 보기</summary>
          <div>{hidden.map((source, index) => <SourceCard key={source.chunk_id} source={source} number={index + 3} onOpenDocument={onOpenDocument} idPrefix={idPrefix} />)}</div>
        </details>
      )}
    </section>
  );
}
