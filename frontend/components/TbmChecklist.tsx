import type { AssessmentResponse, ChecklistItemResponse } from "@/types/assessment";

type TbmChecklistProps = {
  result: AssessmentResponse;
  persistence: "preview" | "saved";
  isSavingAssessment: boolean;
  pendingItemIds: ReadonlySet<string>;
  error: string;
  onSave: () => void;
  onToggle: (item: ChecklistItemResponse, isCompleted: boolean) => void;
};

function checklistItems(result: AssessmentResponse): ChecklistItemResponse[] {
  if (result.checklist_items?.length) return result.checklist_items;
  return result.tbm_checklist.map((content, index) => ({
    id: null,
    sequence: index + 1,
    content,
    is_completed: false,
    completed_by_user_id: null,
    completed_at: null,
  }));
}

function completedAtLabel(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

export default function TbmChecklist({
  result,
  persistence,
  isSavingAssessment,
  pendingItemIds,
  error,
  onSave,
  onToggle,
}: TbmChecklistProps) {
  const items = checklistItems(result);
  const completedCount = items.filter((item) => item.is_completed).length;
  const percentage = items.length ? Math.round((completedCount / items.length) * 100) : 0;
  const isSaved = persistence === "saved";

  return (
    <div className="tbm-workspace">
      <section className={`tbm-state-banner ${isSaved ? "saved" : "preview"}`} aria-live="polite">
        <strong>{isSaved ? "DB에 저장된 TBM 체크리스트" : "아직 저장되지 않은 미리보기입니다"}</strong>
        <span>
          {isSaved
            ? "각 확인 결과는 서버에 저장됩니다. 모든 항목 완료가 작업 승인을 의미하지는 않습니다."
            : "항목을 검토한 뒤 저장해야 실제 확인 상태를 기록할 수 있습니다."}
        </span>
      </section>

      <div className="tbm-progress" aria-label={`TBM 체크리스트 ${completedCount}/${items.length} 완료`}>
        <div>
          <strong>TBM 체크리스트 · {completedCount}/{items.length} 완료</strong>
          <span>{percentage}%</span>
        </div>
        <progress max={Math.max(items.length, 1)} value={completedCount}>
          {percentage}%
        </progress>
      </div>

      <div className="persisted-checklist">
        {items.map((item) => {
          const itemKey = item.id ?? `preview-${item.sequence}`;
          const isPending = item.id ? pendingItemIds.has(item.id) : false;
          const completedLabel = completedAtLabel(item.completed_at);
          return (
            <label className={item.is_completed ? "completed" : ""} key={itemKey}>
              <input
                type="checkbox"
                checked={item.is_completed}
                disabled={!isSaved || !item.id || isPending}
                onChange={(event) => onToggle(item, event.target.checked)}
              />
              <span className="tbm-item-content">
                <strong>{item.sequence}. {item.content}</strong>
                <small>
                  {!isSaved
                    ? "미리보기 · 저장 전에는 체크할 수 없습니다"
                    : isPending
                      ? "서버에 저장 중…"
                      : item.is_completed
                        ? `DB 저장 완료${completedLabel ? ` · ${completedLabel}` : ""}`
                        : "현장 확인 필요"}
                </small>
              </span>
            </label>
          );
        })}
      </div>

      {!isSaved && (
        <button
          type="button"
          className="primary-button tbm-save-button"
          disabled={isSavingAssessment || items.length === 0}
          onClick={onSave}
        >
          {isSavingAssessment ? "DB에 저장 중…" : "저장하고 TBM 시작"}
        </button>
      )}
      {error && <p className="tbm-save-error" role="alert">{error}</p>}
      <p className="tbm-approval-notice">
        체크 완료 여부와 위험성평가 승인 상태는 별개입니다. 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.
      </p>
    </div>
  );
}
