"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import type { AssessmentResponse } from "@/types/assessment";
import { getApiBaseUrl } from "@/lib/api";

type PageMode = "login" | "workspace" | "history";
type FontSize = "small" | "medium" | "large";

type LocalUser = {
  id: string;
  username: string;
  displayName: string;
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

function writeStorage<T>(key: string, value: T): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function removeStorage(key: string) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Authentication remains usable for the current tab when storage is blocked.
  }
}

export default function HomePage() {
  const [page, setPage] = useState<PageMode>("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");

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
    removeStorage(STORAGE_KEYS.session);
    setUsername("");
    setDisplayName("");
    setPage("login");
  }

  if (page === "login") {
    return <LoginScreen onLogin={handleLogin} />;
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

function LoginScreen({ onLogin }: { onLogin: (user: LocalUser) => void }) {
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const [isLoading, setIsLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ employee_number: loginId, password }),
      });
      const payload = await response.json() as { id?: string; employee_number?: string; name?: string; detail?: string };
      if (!response.ok || !payload.id || !payload.employee_number || !payload.name) {
        throw new Error(payload.detail || "로그인에 실패했습니다.");
      }
      onLogin({ id: payload.id, username: payload.employee_number, displayName: payload.name });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "백엔드에 연결할 수 없습니다.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-icon">🦺</div>
        <h1>SafeMaint AI</h1>
        <p>제조설비 정비작업 안전관리 Assistant</p>
        <form onSubmit={submit} className="auth-form">
          <label>
            사원번호(ID)
            <input value={loginId} onChange={(event) => setLoginId(event.target.value)} required />
          </label>
          <label>
            비밀번호
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          </label>
          {error && <p className="error-message">{error}</p>}
          <button className="primary-button" disabled={isLoading} type="submit">{isLoading ? "로그인 중..." : "로그인"}</button>
        </form>
        <a className="secondary-button auth-link" href="/signup">회원가입</a>
        <p className="prototype-note">계정은 SafeMaint 데이터베이스에 안전하게 저장됩니다.</p>
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

  const [isLoading, setIsLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
    setIsLoading(true);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: signupId, display_name: name, password }),
      });
      const payload = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(payload.detail || "회원가입에 실패했습니다.");
      setMessage("회원가입이 완료되었습니다. 로그인 화면으로 이동합니다.");
      setIsError(false);
      window.setTimeout(onComplete, 700);
    } catch (requestError) {
      setMessage(requestError instanceof Error ? requestError.message : "백엔드에 연결할 수 없습니다.");
      setIsError(true);
    } finally {
      setIsLoading(false);
    }
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
          <button className="primary-button" disabled={isLoading} type="submit">{isLoading ? "가입 중..." : "가입하기"}</button>
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
  const [volume, setVolume] = useState(70);
  const [fontSize, setFontSize] = useState<FontSize>("medium");
  const [manuals, setManuals] = useState<string[]>([]);
  const [sitePhotoName, setSitePhotoName] = useState("");
  const [locationStatus, setLocationStatus] = useState("위치 미확인");
  const [activeTab, setActiveTab] = useState<"summary" | "accidents" | "evidence" | "tbm">("summary");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<{ role: "user" | "ai"; text: string }[]>([]);
  const [form, setForm] = useState(initialForm);
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const settings = readStorage<{ volume: number; fontSize: FontSize }>(STORAGE_KEYS.settings, { volume: 70, fontSize: "medium" });
    setVolume(settings.volume);
    setFontSize(settings.fontSize);
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined") writeStorage(STORAGE_KEYS.settings, { volume, fontSize });
  }, [volume, fontSize]);

  useEffect(() => () => {
    const source = audioSourceRef.current;
    if (source) {
      source.onended = null;
      try { source.stop(); } catch { /* already stopped */ }
      source.disconnect();
    }
    void audioContextRef.current?.close();
  }, []);

  const fontClass = useMemo(() => `font-${fontSize}`, [fontSize]);
  const highestRisk = result?.hazards.some((hazard) => hazard.risk_level === "high") ? "high" : result?.hazards.some((hazard) => hazard.risk_level === "medium") ? "medium" : result ? "low" : "pending";
  const accidentTypes = result ? Array.from(new Set(result.hazards.map((hazard) => hazard.accident_type))) : [];

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
      const response = await fetch(`${getApiBaseUrl()}/api/v1/assessments/preview`, {
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

  async function sendChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim()) return;
    const submittedQuestion = question.trim();
    setMessages((current) => [...current, { role: "user", text: submittedQuestion }]);
    setQuestion("");
    setIsChatLoading(true);
    setError("");
    try {
      const context = [
        `사업장: ${form.site_name}`,
        `설비: ${form.equipment_name}`,
        `부품: ${form.component_name || "미입력"}`,
        `작업 종류: ${form.task_type}`,
        `에너지원: ${form.energy_source}`,
        `작업 설명: ${form.description}`,
        `등록 매뉴얼: ${manuals.length ? manuals.join(", ") : "없음"}`,
      ].join("\n");
      const response = await fetch(`${getApiBaseUrl()}/api/v1/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: submittedQuestion, context }),
      });
      const payload = await response.json().catch(() => null) as { answer?: string; detail?: string } | null;
      if (!response.ok || !payload?.answer) {
        throw new Error(payload?.detail || "AI 답변을 생성하지 못했습니다.");
      }
      setMessages((current) => [...current, { role: "ai", text: payload.answer! }]);
      saveHistory(submittedQuestion, payload.answer, "검토 필요");
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "AI 요청 중 오류가 발생했습니다.";
      setMessages((current) => [...current, { role: "ai", text: `오류: ${message}` }]);
      setError(message);
    } finally {
      setIsChatLoading(false);
    }
  }

  function updateField(field: keyof typeof initialForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function addManuals(files: FileList | null) {
    if (!files) return;
    setManuals((current) => Array.from(new Set([...current, ...Array.from(files, (file) => file.name)])));
  }

  function requestLocation() {
    if (!navigator.geolocation) {
      setLocationStatus("위치 기능 미지원");
      return;
    }
    setLocationStatus("위치 확인 중...");
    navigator.geolocation.getCurrentPosition(
      (position) => setLocationStatus(`위치 확인됨 · ${position.coords.latitude.toFixed(4)}, ${position.coords.longitude.toFixed(4)}`),
      () => setLocationStatus("위치 권한이 필요합니다"),
      { enableHighAccuracy: false, timeout: 7000 },
    );
  }

  async function speakGuidance() {
    if (audioSourceRef.current) {
      const source = audioSourceRef.current;
      source.onended = null;
      try { source.stop(); } catch { /* already stopped */ }
      source.disconnect();
      audioSourceRef.current = null;
      setIsSpeaking(false);
      return;
    }

    const latestAnswer = [...messages].reverse().find((message) => message.role === "ai")?.text;
    const text = latestAnswer ?? (result
      ? `현재 분석된 위험요인은 ${result.hazards.length}건입니다. ${result.hazards.map((hazard) => `${hazard.name}. ${hazard.safety_actions.join(". ")}`).join(". ")}`
      : `현재 작업은 ${form.equipment_name}의 ${form.task_type}입니다. 위험성평가를 실행한 뒤 음성 안전 안내를 들을 수 있습니다.`);

    setIsSpeaking(true);
    setError("");
    try {
      // Unlock audio playback while the button click is still an active user gesture.
      const audioContext = audioContextRef.current ?? new AudioContext();
      audioContextRef.current = audioContext;
      await audioContext.resume();

      const response = await fetch(`${getApiBaseUrl()}/api/v1/speech/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, speed: 0.92 }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail || "음성 안내를 생성하지 못했습니다.");
      }
      const audioBuffer = await audioContext.decodeAudioData(await response.arrayBuffer());
      const source = audioContext.createBufferSource();
      const gain = audioContext.createGain();
      gain.gain.value = volume / 100;
      source.buffer = audioBuffer;
      source.connect(gain);
      gain.connect(audioContext.destination);
      source.onended = () => {
        source.disconnect();
        gain.disconnect();
        if (audioSourceRef.current === source) audioSourceRef.current = null;
        setIsSpeaking(false);
      };
      audioSourceRef.current = source;
      source.start(0);
    } catch (speechError) {
      audioSourceRef.current = null;
      setIsSpeaking(false);
      setError(speechError instanceof Error ? speechError.message : "음성 안내 중 오류가 발생했습니다.");
    }
  }

  return (
    <main className={`prototype-shell ${fontClass}`}>
      <header className="prototype-topbar">
        <details className="menu-control">
          <summary className="menu-button">☰ 메뉴</summary>
          <aside className="floating-menu">
            <h2>메뉴</h2>
            <label>🔊 음량 <strong>{volume}</strong><input type="range" min="0" max="100" value={volume} onChange={(event) => setVolume(Number(event.target.value))} /></label>
            <label>글자 크기<select value={fontSize} onChange={(event) => setFontSize(event.target.value as FontSize)}><option value="small">작게</option><option value="medium">보통</option><option value="large">크게</option></select></label>
            <button onClick={() => setMessages([])}>🧹 대화 초기화</button>
            <button onClick={() => setManuals([])}>📚 매뉴얼 목록 초기화</button>
            <button className="logout-button" onClick={onLogout}>🚪 로그아웃</button>
          </aside>
        </details>
        <div><strong>SafeMaint AI</strong><span>작업 전 위험성평가 · 사고예방 · 근거 기반 안전 안내</span></div>
        <div className="user-label"><strong>{displayName}</strong> 님</div>
      </header>

      <section className="quick-toolbar" aria-label="현장 빠른 기능">
        <button type="button" onClick={requestLocation}>📍 현재 위치</button>
        <label className="toolbar-upload">📷 현장 사진<input type="file" accept="image/*" onChange={(event) => setSitePhotoName(event.target.files?.[0]?.name ?? "")} /></label>
        <button type="button" onClick={() => window.alert("음성 입력은 STT 연결 예정입니다.")}>🎤 음성 입력</button>
        <button type="button" onClick={speakGuidance}>{isSpeaking ? "⏹ 음성 중지" : "🔊 음성 안내"}</button>
        <button type="button" onClick={onHistory}>🗂 결과 기록</button>
        <span>{locationStatus}</span>
      </section>

      <section className="field-overview-grid">
        <article className={`risk-overview-card risk-${highestRisk}`}>
          <span className="eyebrow">현재 작업 판단</span>
          <div className="risk-symbol">{highestRisk === "high" ? "🔴" : highestRisk === "medium" ? "🟡" : highestRisk === "low" ? "🟢" : "⚪"}</div>
          <h1>{highestRisk === "high" ? "작업 중지 필요" : highestRisk === "medium" ? "관리자 확인 필요" : highestRisk === "low" ? "기본조치 확인" : "분석 전"}</h1>
          <p>{result ? `위험요인 ${result.hazards.length}건이 분석되었습니다.` : "아래 작업정보와 자료를 입력한 뒤 분석을 시작하세요."}</p>
          <div className="accident-chip-list">{accidentTypes.length ? accidentTypes.map((type) => <span key={type}>⚠ {type}</span>) : <span>사고 유형 대기</span>}</div>
          <small>AI는 작업을 승인하지 않으며 최종 판단은 안전관리자가 수행합니다.</small>
        </article>
        <article className="panel ppe-panel">
          <div className="panel-heading compact-heading"><h2>필수 보호구</h2><span className="panel-tag">현장 확인</span></div>
          <div className="ppe-grid"><span>🪖<b>안전모</b></span><span>🧤<b>보호장갑</b></span><span>🥽<b>보안경</b></span><span>👢<b>안전화</b></span></div>
          <p className="muted-copy">작업과 화학물질 특성에 따라 추가 보호구가 필요할 수 있습니다.</p>
        </article>
      </section>

      <section className="manual-row upgraded-manual-row">
        <label className="file-card">📄 작업 설비 매뉴얼 추가<input type="file" accept="application/pdf" multiple onChange={(event) => addManuals(event.target.files)} /></label>
        <div><strong>{manuals.length ? `${manuals.length}개 매뉴얼 등록됨` : "등록된 매뉴얼 없음"}</strong><span>{sitePhotoName ? `현장 사진: ${sitePhotoName}` : "현장 사진 없음"} · 실제 업로드 API 연결 예정</span></div>
        <div className="document-chip-list">{manuals.map((name) => <span key={name}>📄 {name}<button type="button" aria-label={`${name} 삭제`} onClick={() => setManuals((current) => current.filter((item) => item !== name))}>×</button></span>)}</div>
      </section>

      <section className="chat-stage upgraded-chat">
        <div className="chat-heading"><div><strong>SafeMaint AI 상담</strong><span>분석 결과와 등록 문서를 바탕으로 후속 질문을 입력하세요.</span></div><button type="button" onClick={() => setMessages([])}>대화 지우기</button></div>
        <div className="chat-history">
          {messages.length === 0 ? <div className="chat-empty">💬 작업 내용이나 부품 관련 질문을 입력하세요.</div> : messages.map((message, index) => <div className={`chat-bubble ${message.role}`} key={`${message.role}-${index}`}><strong>{message.role === "user" ? "사용자" : "SafeMaint AI"}</strong>{message.text}</div>)}
        </div>
        <form className="chat-input-row" onSubmit={sendChat}>
          <button type="button" className="icon-action" title="음성 입력" onClick={() => window.alert("음성 입력 기능은 STT 연결 예정입니다.")}>🎤</button>
          <label className="icon-action file-icon" title="사진 첨부">📷<input type="file" accept="image/*" onChange={(event) => setSitePhotoName(event.target.files?.[0]?.name ?? "")} /></label>
          <label className="icon-action file-icon" title="문서 첨부">📎<input type="file" accept="application/pdf" multiple onChange={(event) => addManuals(event.target.files)} /></label>
          <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="예: 전원을 차단하지 않고 센서만 빠르게 교체해도 될까요?" />
          <button type="submit" disabled={isChatLoading}>{isChatLoading ? "답변 생성 중..." : "전송"}</button>
        </form>
      </section>

      <details className="assessment-drawer">
        <summary>규칙 기반 위험성평가 미리보기 열기</summary>
        <section className="search-progress">
          <div className="panel-heading compact-heading"><div><span className="section-number">진행</span><h2>분석 과정</h2></div><span className={isLoading ? "status-pill active" : "status-pill"}>{isLoading ? "진행 중" : result ? "완료" : "대기"}</span></div>
          <div className="progress-steps">{["작업정보 분석", "사고사례 검색", "법령·KOSHA 검색", "매뉴얼 검색", "재정렬", "결과 생성"].map((step, index) => <div className={result ? "done" : isLoading && index < 3 ? "active" : ""} key={step}><span>{result ? "✓" : index + 1}</span><strong>{step}</strong></div>)}</div>
        </section>
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
            <div className="result-tabs">
              <button type="button" className={activeTab === "summary" ? "active" : ""} onClick={() => setActiveTab("summary")}>위험요인</button>
              <button type="button" className={activeTab === "accidents" ? "active" : ""} onClick={() => setActiveTab("accidents")}>유사 사고</button>
              <button type="button" className={activeTab === "evidence" ? "active" : ""} onClick={() => setActiveTab("evidence")}>근거 문서</button>
              <button type="button" className={activeTab === "tbm" ? "active" : ""} onClick={() => setActiveTab("tbm")}>TBM 체크</button>
            </div>
            {!result ? <div className="empty-state"><div className="empty-icon">!</div><h3>아직 분석 결과가 없습니다.</h3><p>왼쪽 작업정보를 확인하고 초안 만들기를 실행해 주세요.</p></div> : activeTab === "summary" ? <AssessmentResult result={result} mode="hazards" /> : activeTab === "accidents" ? <SimilarAccidentPanel result={result} /> : activeTab === "evidence" ? <EvidencePanel result={result} manuals={manuals} /> : <AssessmentResult result={result} mode="tbm" />}
          </section>
        </div>
      </details>

      <footer className="safety-footer">본 결과는 작업 전 검토를 위한 초안이며, 현장 안전관리자의 최종 확인과 승인 없이 작업을 시작할 수 없습니다.</footer>
    </main>
  );
}

function AssessmentResult({ result, mode = "hazards" }: { result: AssessmentResponse; mode?: "hazards" | "tbm" }) {
  const mustStop = result.hazards.some((hazard) => hazard.risk_level === "high");
  const decision = mustStop ? "작업 중지 및 안전관리자 확인 필요" : result.evidence_status === "connected" ? "안전관리자 검토 가능" : "근거 부족으로 판단 불가";

  if (mode === "tbm") return <div className="result-content"><div className="notice">작업 전 팀 단위로 각 항목을 직접 확인하세요.</div><div className="checklist large-checklist"><h3>작업 전 TBM 체크리스트</h3>{result.tbm_checklist.map((item) => <label key={item}><input type="checkbox" /><span>{item}</span></label>)}</div><section className="manager-review"><strong>안전관리자 확인사항</strong><label><input type="checkbox" /> 작업조건과 에너지 차단 여부를 현장에서 재확인했습니다.</label><label><input type="checkbox" /> 근거 문서와 필수 안전조치를 검토했습니다.</label></section></div>;

  return <div className="result-content">
    <section className={`work-decision ${mustStop ? "stop" : result.evidence_status === "connected" ? "review" : "unknown"}`}>
      <span>작업 판단</span><strong>{decision}</strong>
      <p>AI 결과는 작업 승인이 아닙니다. 현장 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.</p>
    </section>
    <div className="notice">{result.disclaimer}</div>
    <div className="hazard-list">{result.hazards.map((hazard) => <article className={`hazard-card ${hazard.risk_level}`} key={hazard.name}><div className="hazard-title"><div><span>{hazard.accident_type}</span><h3>{hazard.name}</h3></div><strong>{levelLabel[hazard.risk_level]} · {hazard.score}점</strong></div><ul>{hazard.safety_actions.map((action) => <li key={action}>{action}</li>)}</ul></article>)}</div>
  </div>;
}

function EvidencePanel({ result, manuals }: { result: AssessmentResponse; manuals: string[] }) {
  return <section className="evidence-section"><div className="evidence-state"><strong>근거 문서 및 출처</strong><span>{result.evidence_status === "connected" ? `${result.evidence.length}건 연결됨` : "검색 근거가 연결되지 않았습니다"}</span></div>{result.evidence.length > 0 ? <div className="evidence-list">{result.evidence.map((item) => <article className="evidence-card" key={item.document_id}><div><span>{item.source_type}</span><strong>{item.title}</strong><small>{item.page ? `${item.page}페이지` : "페이지 정보 없음"}</small></div><p>{item.excerpt}</p>{item.url && <a href={item.url} target="_blank" rel="noreferrer">원문 확인</a>}</article>)}</div> : <><p className="evidence-warning">근거가 없으므로 작업 안전성을 판단할 수 없습니다. 문서 수집 API와 RAG 연결 후 출처가 표시됩니다.</p>{manuals.length > 0 && <div className="pending-document-list"><strong>처리 대기 문서</strong>{manuals.map((name) => <span key={name}>📄 {name}</span>)}</div>}</>}</section>;
}

function SimilarAccidentPanel({ result }: { result: AssessmentResponse }) {
  const types = Array.from(new Set(result.hazards.map((hazard) => hazard.accident_type)));
  return <div className="similar-accident-list">{types.map((type) => <article className="accident-card" key={type}><div><span>관련 사고유형</span><strong>{type} 사고</strong></div><p>현재 규칙 엔진이 감지한 사고유형입니다. 실제 유사 사고 원문과 유사도는 사고사례 검색 API 연결 후 표시됩니다.</p><ul><li>전원 차단 및 LOTO 확인</li><li>위험구역 통제와 관리자 확인</li></ul></article>)}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}
