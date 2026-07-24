import type { ChatSource } from "@/types/chat";

function SourceCard({ source, number }: { source: ChatSource; number: number }) {
  const pageLabel = source.page_start
    ? `${source.page_start}${source.page_end && source.page_end !== source.page_start ? `–${source.page_end}` : ""}페이지`
    : source.page ? `${source.page}페이지` : "페이지 정보 없음";

  return (
    <article className="chat-source-card" id={`chat-source-${number}`}>
      <div className="chat-source-heading">
        <span className="source-number">근거 {number}</span>
        <span>{source.source_type} · 유사도 {(source.similarity * 100).toFixed(1)}%</span>
      </div>
      <strong>{source.original_filename || source.title}</strong>
      <small className="chat-source-location">{[pageLabel, source.section ? `섹션: ${source.section}` : null, source.document_version ? `버전 ${source.document_version}` : null].filter(Boolean).join(" · ")}</small>
      <p>{source.excerpt}</p>
      <div className="source-card-footer">
        {source.url && <a href={source.url} target="_blank" rel="noreferrer">원문 확인</a>}
        <details className="source-technical"><summary>식별 정보</summary><small>문서 ID {source.document_id}{source.document_version_id ? ` · 버전 ID ${source.document_version_id}` : ""}</small></details>
      </div>
    </article>
  );
}

export default function ChatSources({ sources }: { sources: ChatSource[] }) {
  if (!sources.length) return null;
  const visible = sources.slice(0, 2);
  const hidden = sources.slice(2);

  return (
    <section className="chat-source-list" aria-label={`검색 근거 ${sources.length}건`}>
      <div className="chat-source-list-heading"><strong>검색 근거</strong><span>{sources.length}건</span></div>
      {visible.map((source, index) => <SourceCard key={source.chunk_id} source={source} number={index + 1} />)}
      {hidden.length > 0 && (
        <details className="additional-sources">
          <summary>나머지 근거 {hidden.length}건 보기</summary>
          <div>{hidden.map((source, index) => <SourceCard key={source.chunk_id} source={source} number={index + 3} />)}</div>
        </details>
      )}
    </section>
  );
}
