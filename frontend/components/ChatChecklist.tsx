"use client";

import { useEffect, useState } from "react";

import type { ChatChecklistItem } from "@/types/chat";


function initialCheckedIndices(items: ChatChecklistItem[]): Set<number> {
  const indices = new Set<number>();
  items.forEach((item, index) => {
    if (item.is_completed) indices.add(index);
  });
  return indices;
}

export default function ChatChecklist({
  items,
  savedAssessmentId,
  isSaving,
  onSave,
}: {
  items: ChatChecklistItem[];
  savedAssessmentId: string | null;
  isSaving: boolean;
  onSave: (checkedIndices: number[]) => void;
}) {
  const [checked, setChecked] = useState<Set<number>>(() => initialCheckedIndices(items));
  useEffect(() => setChecked(initialCheckedIndices(items)), [items]);
  if (!items.length) return <p className="answer-tab-empty">확인된 TBM 항목이 없습니다.</p>;

  const isSaved = savedAssessmentId !== null;
  return (
    <div className="maintenance-checklist">
      <div className="structured-section-title">
        <span>{isSaved ? "DB에 저장됨" : "채팅 미리보기"}</span>
      </div>
      <div className="chat-checklist-items">
        {items.map((item, index) => {
          const isChecked = checked.has(index);
          return (
            <label className={isChecked ? "checked" : ""} key={`${item.sequence}-${item.content}`}>
              <input
                type="checkbox"
                checked={isChecked}
                onChange={(event) => setChecked((current) => {
                  const next = new Set(current);
                  if (event.target.checked) next.add(index);
                  else next.delete(index);
                  return next;
                })}
              />
              <span>{item.sequence}. {item.content}</span>
              {item.is_required && <strong>필수</strong>}
            </label>
          );
        })}
      </div>
      <button
        type="button"
        className="chat-checklist-save"
        onClick={() => onSave(Array.from(checked))}
        disabled={isSaving}
      >
        {isSaving ? "저장 중..." : isSaved ? "변경사항 저장" : "이 체크리스트 저장"}
      </button>
      <p className="structured-note">
        {isSaved
          ? "체크 상태를 바꾼 뒤 변경사항 저장을 누르면 DB에 반영됩니다."
          : "현재 항목은 미리보기입니다. 저장하면 위험성평가에 등록되며 이후에도 진행 상태를 갱신할 수 있습니다."}
      </p>
    </div>
  );
}
