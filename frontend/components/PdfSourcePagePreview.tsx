import type { ChatSource } from "@/types/chat";

function sourcePageLabel(source: ChatSource): string {
  if (source.page_start) {
    const end = source.page_end;
    return end && end !== source.page_start
      ? `${source.page_start}–${end}페이지`
      : `${source.page_start}페이지`;
  }
  return source.page ? `${source.page}페이지` : "페이지 정보 없음";
}

function documentKey(source: ChatSource): string {
  return `${source.document_id}::${source.document_version_id ?? ""}`;
}

// 카드 의미(예: 정격/성능)에 실제로 관련된 근거 PDF 페이지만 보여준다. 페이지 안 내용은
// 미리보기 없이 "원문 전체 보기" 버튼으로만 확인한다(DocumentViewerModal이 이미 정확한
// 페이지로 열어 주므로 여기선 그 호출만 한다).
export default function PdfSourcePagePreview({
  pages,
  sourceNumbers,
  sourceAnchorId,
  onSelectSource,
  onOpenDocument,
  emptyMessage = "확인된 내용이 없습니다.",
}: {
  pages: ChatSource[];
  sourceNumbers: ReadonlyMap<string, number>;
  sourceAnchorId: (number: number) => string;
  onSelectSource: (number: number) => void;
  onOpenDocument: (source: ChatSource) => void;
  emptyMessage?: string;
}) {
  if (!pages.length) {
    return <p className="answer-tab-empty">{emptyMessage}</p>;
  }

  return (
    <ul className="pdf-source-page-list">
      {pages.map((page) => {
        const number = sourceNumbers.get(page.chunk_id);
        const pageNumber = page.page_start ?? page.page ?? null;
        return (
          <li key={`${documentKey(page)}-${pageNumber ?? page.chunk_id}`} className="pdf-source-page-card">
            <div className="pdf-source-page-heading">
              <strong>{page.original_filename || page.title}</strong>
              <span>
                {[sourcePageLabel(page), page.section ? `섹션: ${page.section}` : null]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </div>
            {number !== undefined && (
              <a
                href={`#${sourceAnchorId(number)}`}
                className="pdf-source-page-evidence-link"
                onClick={(event) => {
                  event.preventDefault();
                  onSelectSource(number);
                }}
              >
                근거 [{number}]
              </a>
            )}
            <button type="button" className="pdf-source-page-open" onClick={() => onOpenDocument(page)}>
              원문 전체 보기
            </button>
          </li>
        );
      })}
    </ul>
  );
}
