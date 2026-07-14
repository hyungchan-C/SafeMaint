"use client";

import { FormEvent, useState } from "react";

import type { AssessmentResponse } from "@/types/assessment";


const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const initialForm = {
  site_name: "A공장",
  equipment_name: "컨베이어 CV-203",
  manufacturer: "",
  model_number: "CV-203",
  component_name: "벨트",
  task_type: "이물질 제거",
  energy_source: "전기",
  description: "컨베이어를 정지한 뒤 벨트에 낀 이물질을 제거합니다.",
};

const levelLabel = {
  low: "낮음",
  medium: "보통",
  high: "높음",
};


export default function DashboardPage() {
  const [form, setForm] = useState(initialForm);
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setError("");

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/assessments/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form,
          manufacturer: form.manufacturer || null,
          energy_sources: form.energy_source ? [form.energy_source] : [],
        }),
      });

      if (!response.ok) {
        throw new Error("분석 요청에 실패했습니다. 백엔드 실행 상태를 확인해 주세요.");
      }

      setResult((await response.json()) as AssessmentResponse);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "알 수 없는 오류가 발생했습니다.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  function updateField(field: keyof typeof initialForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark">SM</div>
        <div>
          <strong>SafeMaint AI</strong>
          <span>Safety workspace</span>
        </div>
        <nav aria-label="주요 메뉴">
          <a className="active" href="#dashboard">대시보드</a>
          <a href="#assessment">작업 위험성평가</a>
          <a href="#checklist">TBM 체크리스트</a>
          <a href="#evidence">안전문서</a>
        </nav>
        <p className="sidebar-note">
          초기 MVP · 규칙 기반 미리보기
        </p>
      </aside>

      <section className="content" id="dashboard">
        <header className="page-header">
          <div>
            <span className="eyebrow">A공장 · 안전관리 대시보드</span>
            <h1>작업 전 위험요인을 먼저 확인하세요.</h1>
            <p>설비와 작업 내용을 입력하면 위험성평가 초안을 생성합니다.</p>
          </div>
          <span className="status-pill">개발 환경</span>
        </header>

        <div className="metric-grid" aria-label="작업 요약">
          <MetricCard label="오늘 작업" value="0" helper="DB 연결 전" />
          <MetricCard label="고위험 작업" value="0" helper="관리자 확인 필요" tone="danger" />
          <MetricCard label="체크리스트 완료" value="0%" helper="저장 기능 연결 전" />
          <MetricCard label="검토 대기" value="0" helper="승인 흐름은 후순위" />
        </div>

        <div className="workspace-grid">
          <section className="panel" id="assessment">
            <div className="panel-heading">
              <div>
                <span className="section-number">01</span>
                <h2>작업정보 입력</h2>
              </div>
              <span className="panel-tag">필수</span>
            </div>

            <form onSubmit={handleSubmit}>
              <div className="form-grid">
                <Field label="사업장">
                  <input value={form.site_name} onChange={(e) => updateField("site_name", e.target.value)} required />
                </Field>
                <Field label="설비명">
                  <input value={form.equipment_name} onChange={(e) => updateField("equipment_name", e.target.value)} required />
                </Field>
                <Field label="제조사">
                  <input value={form.manufacturer} onChange={(e) => updateField("manufacturer", e.target.value)} placeholder="선택 입력" />
                </Field>
                <Field label="모델·부품번호">
                  <input value={form.model_number} onChange={(e) => updateField("model_number", e.target.value)} />
                </Field>
                <Field label="부품">
                  <input value={form.component_name} onChange={(e) => updateField("component_name", e.target.value)} />
                </Field>
                <Field label="작업 종류">
                  <input value={form.task_type} onChange={(e) => updateField("task_type", e.target.value)} required />
                </Field>
                <Field label="주요 에너지원">
                  <select value={form.energy_source} onChange={(e) => updateField("energy_source", e.target.value)}>
                    <option value="전기">전기</option>
                    <option value="기계">기계</option>
                    <option value="압력">압력</option>
                    <option value="열">열</option>
                    <option value="">미확인</option>
                  </select>
                </Field>
              </div>

              <Field label="작업 설명">
                <textarea
                  value={form.description}
                  onChange={(e) => updateField("description", e.target.value)}
                  minLength={5}
                  rows={4}
                  required
                />
              </Field>

              {error && <p className="error-message">{error}</p>}
              <button className="primary-button" disabled={isLoading} type="submit">
                {isLoading ? "분석 중..." : "위험성평가 초안 만들기"}
              </button>
            </form>
          </section>

          <section className="panel result-panel" aria-live="polite">
            <div className="panel-heading">
              <div>
                <span className="section-number">02</span>
                <h2>분석 결과</h2>
              </div>
              <span className="panel-tag muted">초안</span>
            </div>

            {!result ? (
              <div className="empty-state">
                <div className="empty-icon">!</div>
                <h3>아직 분석 결과가 없습니다.</h3>
                <p>왼쪽 작업정보를 확인하고 초안 만들기를 실행해 주세요.</p>
              </div>
            ) : (
              <div className="result-content">
                <div className="notice">{result.disclaimer}</div>

                <div className="hazard-list">
                  {result.hazards.map((hazard) => (
                    <article className={`hazard-card ${hazard.risk_level}`} key={hazard.name}>
                      <div className="hazard-title">
                        <div>
                          <span>{hazard.accident_type}</span>
                          <h3>{hazard.name}</h3>
                        </div>
                        <strong>{levelLabel[hazard.risk_level]} · {hazard.score}점</strong>
                      </div>
                      <ul>
                        {hazard.safety_actions.map((action) => <li key={action}>{action}</li>)}
                      </ul>
                    </article>
                  ))}
                </div>

                <div className="checklist" id="checklist">
                  <h3>작업 전 TBM 체크리스트</h3>
                  {result.tbm_checklist.map((item) => (
                    <label key={item}>
                      <input type="checkbox" />
                      <span>{item}</span>
                    </label>
                  ))}
                </div>

                <div className="evidence-state" id="evidence">
                  <strong>문서 근거</strong>
                  <span>
                    {result.evidence_status === "connected"
                      ? `${result.evidence.length}건 연결됨`
                      : "하이브리드 RAG 연결 전"}
                  </span>
                </div>
              </div>
            )}
          </section>
        </div>
      </section>
    </main>
  );
}


function MetricCard({
  label,
  value,
  helper,
  tone = "default",
}: {
  label: string;
  value: string;
  helper: string;
  tone?: "default" | "danger";
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{helper}</small>
    </article>
  );
}


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
