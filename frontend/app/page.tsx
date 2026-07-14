"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import type { AssessmentResponse } from "@/types/assessment";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type PageMode = "login" | "signup" | "workspace" | "history";
type FontSize = "small" | "medium" | "large";

type LocalUser = {
  username: string;
  displayName: string;
  password: string;
};

type HistoryItem = {
  id: string;
  createdAt: string;
  username: string;
  question: string;
  summary: string;
  riskLabel: string;
};

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

const levelLabel = { low: "낮음", medium: "보통", high: "높음" } as const;

const STORAGE_KEYS = {
  users: "safemaint.users",
  session: "safemaint.session",
  history: "safemaint.history",
  settings: "safemaint.settings",
};

function readStorage<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const value = window.localStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeStorage<T>(key: string, value: T) {
  window.localStorage.setItem(key, JSON.stringify(value));
}

export default function HomePage() {
  // Temporary prototype mode: open the workspace without requiring an account.
  // Keep the authentication screens in place so backend authentication can be
  // connected later without rebuilding the UI flow.
  const [page, setPage] = useState<PageMode>("workspace");
  const [username, setUsername] = useState("guest");
  const [displayName, setDisplayName] = useState("게스트");

  useEffect(() => {
    const session = readStorage<{ username: string; displayName: string } | null>(STORAGE_KEYS.session, null);
    if (session) {
      setUsername(session.username);
      setDisplayName(session.displayName);
      setPage("workspace");
    }
  }, []);

  function handleLogin(user: LocalUser) {
    setUsername(user.username);
    setDisplayName(user.displayName);
    writeStorage(STORAGE_KEYS.session, { username: user.username, displayName: user.displayName });
    setPage("workspace");
  }

  function handleLogout() {
    window.localStorage.removeItem(STORAGE_KEYS.session);
    setUsername("guest");
    setDisplayName("게스트");
    setPage("workspace");
  }

  if (page === "login") {
    return <LoginScreen onLogin={handleLogin} onSignup={() => setPage("signup")} />;
  }

  if (page === "signup") {
    return <SignupScreen onComplete={() => setPage("login")} onBack={() => setPage("login")} />;
  }

  if (page === "history") {
    return <HistoryScreen username={username} onBack={() => setPage("workspace")} />;
  }

  return (
    <WorkspaceScreen
      username={username}
      displayName={displayName}
      onHistory={() => setPage("history")}
      onLogout={handleLogout}
    />
  );
}

function LoginScreen({ onLogin, onSignup }: { onLogin: (user: LocalUser) => void; onSignup: () => void }) {
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const users = readStorage<LocalUser[]>(STORAGE_KEYS.users, []);
    const user = users.find((item) => item.username === loginId && item.password === password);
    if (!user) {
      setError("아이디 또는 비밀번호가 올바르지 않습니다.");
      return;
    }
    onLogin(user);
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-icon">🦺</div>
        <h1>SafeMaint AI</h1>
        <p>제조설비 정비작업 안전관리 Assistant</p>
        <form onSubmit={submit} className="auth-form">
          <label>
            아이디
            <input value={loginId} onChange={(event) => setLoginId(event.target.value)} required />
          </label>
          <label>
            비밀번호
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          </label>
          {error && <p className="error-message">{error}</p>}
          <button className="primary-button" type="submit">로그인</button>
        </form>
        <button className="secondary-button" onClick={onSignup}>회원가입</button>
        <p className="prototype-note">초기 프로토타입에서는 브라우저 저장소로 계정 흐름을 확인합니다.</p>
      </section>
    </main>
  );
}

function SignupScreen({ onComplete, onBack }: { onComplete: () => void; onBack: () => void }) {
  const [name, setName] = useState("");
  const [signupId, setSignupId] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const users = readStorage<LocalUser[]>(STORAGE_KEYS.users, []);
    if (signupId.length < 4 || password.length < 8) {
      setMessage("아이디는 4자 이상, 비밀번호는 8자 이상 입력해 주세요.");
      setIsError(true);
      return;
    }
    if (password !== confirm) {
      setMessage("비밀번호가 일치하지 않습니다.");
      setIsError(true);
      return;
    }
    if (!agreed) {
      setMessage("이용 안내에 동의해 주세요.");
      setIsError(true);
      return;
    }
    if (users.some((item) => item.username === signupId)) {
      setMessage("이미 사용 중인 아이디입니다.");
      setIsError(true);
      return;
    }
    writeStorage(STORAGE_KEYS.users, [
      ...users,
      { username: signupId, displayName: name || signupId, password },
    ]);
    setMessage("회원가입이 완료되었습니다. 로그인 화면으로 이동합니다.");
    setIsError(false);
    window.setTimeout(onComplete, 600);
  }

  return (
    <main className="auth-shell">
      <section className="auth-card wide">
        <h1>회원가입</h1>
        <form onSubmit={submit} className="auth-form">
          <label>이름<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
          <label>아이디<input value={signupId} onChange={(event) => setSignupId(event.target.value)} required /></label>
          <label>비밀번호<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
          <label>비밀번호 확인<input type="password" value={confirm} onChange={(event) => setConfirm(event.target.value)} required /></label>
          <label className="checkbox-line"><input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />서비스 이용 및 개인정보 처리 안내에 동의합니다.</label>
          {message && <p className={isError ? "error-message" : "success-message"}>{message}</p>}
          <button className="primary-button" type="submit">가입하기</button>
        </form>
        <button className="secondary-button" onClick={onBack}>로그인 화면으로</button>
      </section>
    </main>
  );
}

function HistoryScreen({ username, onBack }: { username: string; onBack: () => void }) {
  const items = readStorage<HistoryItem[]>(STORAGE_KEYS.history, []).filter((item) => item.username === username).reverse();
  return (
    <main className="history-shell">
      <header className="simple-header">
        <div><span className="eyebrow">SafeMaint AI</span><h1>결과 기록</h1></div>
        <button className="secondary-button compact" onClick={onBack}>메인으로</button>
      </header>
      <section className="history-list">
        {items.length === 0 ? (
          <div className="panel empty-state"><h2>저장된 기록이 없습니다.</h2><p>위험성평가 또는 채팅 결과가 생성되면 이곳에 표시됩니다.</p></div>
        ) : items.map((item) => (
          <article className="history-card" key={item.id}>
            <div><strong>{item.question}</strong><span>{item.createdAt}</span></div>
            <p>{item.summary}</p>
            <span className="status-pill">위험등급 {item.riskLabel}</span>
          </article>
        ))}
      </section>
    </main>
  );
}

function WorkspaceScreen({
  username,
  displayName,
  onHistory,
  onLogout,
}: {
  username: string;
  displayName: string;
  onHistory: () => void;
  onLogout: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [volume, setVolume] = useState(70);
  const [fontSize, setFontSize] = useState<FontSize>("medium");
  const [manualName, setManualName] = useState("");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<{ role: "user" | "ai"; text: string }[]>([]);
  const [form, setForm] = useState(initialForm);
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const settings = readStorage<{ volume: number; fontSize: FontSize }>(STORAGE_KEYS.settings, { volume: 70, fontSize: "medium" });
    setVolume(settings.volume);
    setFontSize(settings.fontSize);
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined") writeStorage(STORAGE_KEYS.settings, { volume, fontSize });
  }, [volume, fontSize]);

  const fontClass = useMemo(() => `font-${fontSize}`, [fontSize]);

  function saveHistory(questionText: string, summary: string, riskLabel: string) {
    const current = readStorage<HistoryItem[]>(STORAGE_KEYS.history, []);
    const next: HistoryItem = {
      id: crypto.randomUUID(), username, createdAt: new Date().toLocaleString("ko-KR"),
      question: questionText, summary, riskLabel,
    };
    writeStorage(STORAGE_KEYS.history, [...current, next]);
  }

  async function handleAssessment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/assessments/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, manufacturer: form.manufacturer || null, energy_sources: form.energy_source ? [form.energy_source] : [] }),
      });
      if (!response.ok) throw new Error("분석 요청에 실패했습니다. 백엔드 실행 상태를 확인해 주세요.");
      const payload = (await response.json()) as AssessmentResponse;
      setResult(payload);
      const highest = payload.hazards.some((hazard) => hazard.risk_level === "high") ? "높음" : payload.hazards.some((hazard) => hazard.risk_level === "medium") ? "보통" : "낮음";
      saveHistory(form.description, `위험요인 ${payload.hazards.length}건, TBM 체크리스트 ${payload.tbm_checklist.length}건`, highest);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "알 수 없는 오류가 발생했습니다.");
    } finally {
      setIsLoading(false);
    }
  }

  function sendChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim()) return;
    const answer = manualName
      ? `등록된 매뉴얼(${manualName})과 공통 안전자료를 검색할 예정입니다. 현재 프로토타입에서는 전원 차단, LOTO, 잔류에너지 제거 여부를 먼저 확인하세요.`
      : "설비·부품 매뉴얼을 등록하면 해당 문서 근거와 공통 안전자료를 함께 검색합니다. 현재 정보만으로는 작업 승인 여부를 확정할 수 없습니다.";
    setMessages((current) => [...current, { role: "user", text: question.trim() }, { role: "ai", text: answer }]);
    saveHistory(question.trim(), answer, "검토 필요");
    setQuestion("");
  }

  function updateField(field: keyof typeof initialForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  return (
    <main className={`prototype-shell ${fontClass}`}>
      <header className="prototype-topbar">
        <button className="menu-button" onClick={() => setMenuOpen((value) => !value)}>☰ 메뉴</button>
        <div><strong>SafeMaint AI</strong><span>작업 전 위험성평가 · 사고예방 · 근거 기반 안전 안내</span></div>
        <div className="user-label"><strong>{displayName}</strong> 님</div>
      </header>

      {menuOpen && (
        <aside className="floating-menu">
          <h2>메뉴</h2>
          <label>🔊 음량 <strong>{volume}</strong><input type="range" min="0" max="100" value={volume} onChange={(event) => setVolume(Number(event.target.value))} /></label>
          <label>글자 크기<select value={fontSize} onChange={(event) => setFontSize(event.target.value as FontSize)}><option value="small">작게</option><option value="medium">보통</option><option value="large">크게</option></select></label>
          <button onClick={onHistory}>📋 결과 기록 확인</button>
          <button onClick={() => setMessages([])}>🧹 대화 초기화</button>
          <button className="logout-button" onClick={onLogout}>🚪 로그아웃</button>
        </aside>
      )}

      <section className="visual-stage">
        <div className="avatar-placeholder">🧑‍🏭</div>
        <h1>AI 안전관리 화면</h1>
        <p>아바타 · 검색 결과 · 현장 이미지 · 안내 영상이 표시되는 영역입니다.</p>
        {result && <span className="result-badge">최신 분석 결과: 위험요인 {result.hazards.length}건</span>}
      </section>

      <section className="manual-row">
        <label className="file-card">📄 작업 설비 매뉴얼 등록<input type="file" accept="application/pdf" onChange={(event) => setManualName(event.target.files?.[0]?.name ?? "")} /></label>
        <div><strong>{manualName || "등록된 매뉴얼 없음"}</strong><span>실제 업로드 API는 백엔드 문서 수집 기능과 연결 예정입니다.</span></div>
      </section>

      <section className="chat-stage">
        <div className="chat-history">
          {messages.length === 0 ? <div className="chat-empty">💬 작업 내용이나 부품 관련 질문을 입력하세요.</div> : messages.map((message, index) => <div className={`chat-bubble ${message.role}`} key={`${message.role}-${index}`}><strong>{message.role === "user" ? "사용자" : "SafeMaint AI"}</strong>{message.text}</div>)}
        </div>
        <form className="chat-input-row" onSubmit={sendChat}>
          <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="예: 컨베이어 내부 센서를 교체하려고 합니다. 작업 전 확인사항을 알려주세요." />
          <button type="submit">전송</button>
        </form>
      </section>

      <details className="assessment-drawer">
        <summary>규칙 기반 위험성평가 미리보기 열기</summary>
        <div className="workspace-grid embedded">
          <section className="panel">
            <div className="panel-heading"><div><span className="section-number">01</span><h2>작업정보 입력</h2></div><span className="panel-tag">필수</span></div>
            <form onSubmit={handleAssessment}>
              <div className="form-grid">
                <Field label="사업장"><input value={form.site_name} onChange={(e) => updateField("site_name", e.target.value)} required /></Field>
                <Field label="설비명"><input value={form.equipment_name} onChange={(e) => updateField("equipment_name", e.target.value)} required /></Field>
                <Field label="제조사"><input value={form.manufacturer} onChange={(e) => updateField("manufacturer", e.target.value)} placeholder="선택 입력" /></Field>
                <Field label="모델·부품번호"><input value={form.model_number} onChange={(e) => updateField("model_number", e.target.value)} /></Field>
                <Field label="부품"><input value={form.component_name} onChange={(e) => updateField("component_name", e.target.value)} /></Field>
                <Field label="작업 종류"><input value={form.task_type} onChange={(e) => updateField("task_type", e.target.value)} required /></Field>
                <Field label="주요 에너지원"><select value={form.energy_source} onChange={(e) => updateField("energy_source", e.target.value)}><option value="전기">전기</option><option value="기계">기계</option><option value="압력">압력</option><option value="열">열</option><option value="">미확인</option></select></Field>
              </div>
              <Field label="작업 설명"><textarea value={form.description} onChange={(e) => updateField("description", e.target.value)} minLength={5} rows={4} required /></Field>
              {error && <p className="error-message">{error}</p>}
              <button className="primary-button" disabled={isLoading} type="submit">{isLoading ? "분석 중..." : "위험성평가 초안 만들기"}</button>
            </form>
          </section>
          <section className="panel result-panel" aria-live="polite">
            <div className="panel-heading"><div><span className="section-number">02</span><h2>분석 결과</h2></div><span className="panel-tag muted">초안</span></div>
            {!result ? <div className="empty-state"><div className="empty-icon">!</div><h3>아직 분석 결과가 없습니다.</h3><p>왼쪽 작업정보를 확인하고 초안 만들기를 실행해 주세요.</p></div> : <AssessmentResult result={result} />}
          </section>
        </div>
      </details>

      <footer className="safety-footer">본 결과는 작업 전 검토를 위한 초안이며, 현장 안전관리자의 최종 확인과 승인 없이 작업을 시작할 수 없습니다.</footer>
    </main>
  );
}

function AssessmentResult({ result }: { result: AssessmentResponse }) {
  return <div className="result-content"><div className="notice">{result.disclaimer}</div><div className="hazard-list">{result.hazards.map((hazard) => <article className={`hazard-card ${hazard.risk_level}`} key={hazard.name}><div className="hazard-title"><div><span>{hazard.accident_type}</span><h3>{hazard.name}</h3></div><strong>{levelLabel[hazard.risk_level]} · {hazard.score}점</strong></div><ul>{hazard.safety_actions.map((action) => <li key={action}>{action}</li>)}</ul></article>)}</div><div className="checklist"><h3>작업 전 TBM 체크리스트</h3>{result.tbm_checklist.map((item) => <label key={item}><input type="checkbox" /><span>{item}</span></label>)}</div><div className="evidence-state"><strong>문서 근거</strong><span>{result.evidence_status === "connected" ? `${result.evidence.length}건 연결됨` : "하이브리드 RAG 연결 전"}</span></div></div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}
