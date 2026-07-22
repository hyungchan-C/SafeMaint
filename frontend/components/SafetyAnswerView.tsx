type Block =
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] };

type SectionTone = "default" | "decision" | "risk" | "tbm" | "stop" | "evidence";

type AnswerSection = {
  title: string | null;
  tone: SectionTone;
  blocks: Block[];
};

const HEADING_TONES: Array<[RegExp, SectionTone]> = [
  [/작업\s*판단|판단\s*결과/, "decision"],
  [/주요\s*위험|위험\s*요인/, "risk"],
  [/TBM|체크리스트|작업\s*전\s*확인|확인사항/i, "tbm"],
  [/작업\s*중지|중지\s*기준/, "stop"],
  [/근거|출처|추가\s*확인|부족한\s*근거/, "evidence"],
];

function cleanInlineText(value: string): string {
  return value
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .trim();
}

function cleanHeading(line: string): string | null {
  const trimmed = line.trim();
  const markdownHeading = trimmed.match(/^#{1,6}\s+(.+)$/);
  const boldHeading = trimmed.match(/^\*\*(.+?)\*\*\s*:?\s*$/);
  const bracketHeading = trimmed.match(/^\[([^\]]+)\]\s*:?\s*$/);
  const plainHeading = trimmed.match(
    /^(작업\s*판단|판단\s*결과|주요\s*위험(?:\s*요인)?|위험\s*요인|TBM\s*체크리스트|작업\s*중지\s*기준|근거(?:\s*및\s*추가\s*확인)?|출처|추가\s*확인)\s*:?\s*$/i,
  );
  const shortColonHeading = trimmed.length <= 60 ? trimmed.match(/^(.+?)[:：]\s*$/) : null;
  const value = markdownHeading?.[1]
    ?? boldHeading?.[1]
    ?? bracketHeading?.[1]
    ?? plainHeading?.[1]
    ?? shortColonHeading?.[1];
  return value ? cleanInlineText(value) || null : null;
}

function toneForHeading(title: string): SectionTone {
  return HEADING_TONES.find(([pattern]) => pattern.test(title))?.[1] ?? "default";
}

function sentenceParagraphs(answer: string): string[] {
  if (answer.length < 180) return [answer];
  const sentences = answer.match(/[^.!?。！？]+[.!?。！？]+|[^.!?。！？]+$/gu) ?? [];
  if (sentences.length < 2) return [answer];
  return sentences.map((sentence) => sentence.trim()).filter(Boolean);
}

export function parseSafetyAnswer(answer: string): AnswerSection[] {
  const normalized = answer.replace(/\r\n?/g, "\n").trim();
  if (!normalized) return [];

  const lines = normalized.split("\n");
  const sections: AnswerSection[] = [{ title: null, tone: "default", blocks: [] }];
  let current = sections[0];
  let recognizedStructure = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    const heading = cleanHeading(trimmed);
    if (heading) {
      recognizedStructure = true;
      current = { title: heading, tone: toneForHeading(heading), blocks: [] };
      sections.push(current);
      continue;
    }

    const checklistItem = trimmed.match(/^\[([ xX])\]\s*(.+)$/u);
    const listItem = trimmed.match(/^(?:(\d+)[.)]|[-*•✓✔☐☑])\s*(.+)$/u);
    if (checklistItem?.[2]) {
      recognizedStructure = true;
      const previous = current.blocks.at(-1);
      const marker = checklistItem[1].toLowerCase() === "x" ? "☑" : "☐";
      const item = `${marker} ${cleanInlineText(checklistItem[2])}`;
      if (previous?.kind === "list" && !previous.ordered) {
        previous.items.push(item);
      } else {
        current.blocks.push({ kind: "list", ordered: false, items: [item] });
      }
      continue;
    }
    if (listItem?.[2]) {
      recognizedStructure = true;
      const ordered = Boolean(listItem[1]);
      const previous = current.blocks.at(-1);
      if (previous?.kind === "list" && previous.ordered === ordered) {
        previous.items.push(cleanInlineText(listItem[2]));
      } else {
        current.blocks.push({ kind: "list", ordered, items: [cleanInlineText(listItem[2])] });
      }
      continue;
    }

    current.blocks.push({ kind: "paragraph", text: cleanInlineText(trimmed) });
  }

  const populated = sections.filter((section) => section.blocks.length > 0 || section.title);
  if (!recognizedStructure && lines.length === 1) {
    return [
      {
        title: null,
        tone: "default",
        blocks: sentenceParagraphs(normalized).map((text) => ({ kind: "paragraph", text })),
      },
    ];
  }
  return populated;
}

export default function SafetyAnswerView({ answer }: { answer: string }) {
  const sections = parseSafetyAnswer(answer);
  if (sections.length === 0) return null;
  const alreadyHasDisclaimer = /작업\s*승인이\s*아닙니다/.test(answer);

  return (
    <div className="safety-answer-view">
      {sections.map((section, sectionIndex) => (
        <section
          className={`safety-answer-section ${section.tone}`}
          key={`${section.title ?? "answer"}-${sectionIndex}`}
        >
          {section.title && <h4>{section.title}</h4>}
          {section.blocks.map((block, blockIndex) => {
            if (block.kind === "list") {
              const ListTag = block.ordered ? "ol" : "ul";
              return (
                <ListTag key={`list-${blockIndex}`}>
                  {block.items.map((item, itemIndex) => (
                    <li key={`${item}-${itemIndex}`}>{item}</li>
                  ))}
                </ListTag>
              );
            }
            return <p key={`paragraph-${blockIndex}`}>{block.text}</p>;
          })}
        </section>
      ))}
      {!alreadyHasDisclaimer && (
        <p className="safety-answer-disclaimer">
          이 안내는 작업 승인이 아닙니다. 현장 상태와 제조사 매뉴얼을 확인하고 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.
        </p>
      )}
    </div>
  );
}
