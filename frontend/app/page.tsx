"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import DocumentReviewPanel from "@/components/DocumentReviewPanel";
import SafetyAnswerView from "@/components/SafetyAnswerView";
import StructuredChatAnswer from "@/components/StructuredChatAnswer";
import TbmChecklist from "@/components/TbmChecklist";
import type {
  AssessmentResponse,
  ChecklistItemResponse,
  ChecklistItemUpdateResponse,
} from "@/types/assessment";
import type { CatalogCandidate, ChatMessage, ChatResponse } from "@/types/chat";
import type { UserDocumentSummary } from "@/types/documents";
import { DOCUMENT_STATUS_LABELS } from "@/types/documents";
import type { GpsCheckResponse, VirtualEquipment } from "@/types/gps";
import { getApiBaseUrl } from "@/lib/api";

type PageMode = "login" | "workspace" | "history";
type FontSize = "small" | "medium" | "large";

type LocalUser = {
  id: string;
  username: string;
  displayName: string;
  accessToken: string;
};

type StoredSession = {
  username: string;
  displayName: string;
  accessToken: string;
};

type LoginApiResponse = {
  access_token?: string;
  user?: {
    id?: string;
    employee_number?: string;
    name?: string;
  };
  detail?: string;
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
  site_name: "",
  equipment_name: "",
  manufacturer: "",
  model_number: "",
  component_name: "",
  task_type: "",
  energy_source: "",
  description: "",
};

const levelLabel = { low: "낮음", medium: "보통", high: "높음" } as const;
const ppeItems = ["안전모", "보호장갑", "보안경", "안전화"] as const;

// 위경도 ↔ 미터 변환(근사). 위경도 1도당 거리는 위도에 따라 달라지므로
// 경도는 현재 위도의 코사인으로 보정한다. 좁은 지역(수백m 이내) 가정.
const METERS_PER_DEG_LAT = 111_320;
function metersPerDegLon(latDeg: number) {
  return METERS_PER_DEG_LAT * Math.cos((latDeg * Math.PI) / 180);
}

const GPS_MAP_SIZE_PX = 320;
const GPS_MAP_SCALE_PX_PER_M = 2;
const GPS_EQUIPMENT_RADIUS_M = 30;
const GPS_MAP_MARKER_EDGE_PADDING_PX = 20;

function metersOffsetFromCenter(center: { lat: number; lon: number }, lat: number, lon: number) {
  return {
    x: (lon - center.lon) * metersPerDegLon(center.lat),
    y: (center.lat - lat) * METERS_PER_DEG_LAT,
  };
}

// 지도 박스는 overflow: hidden이라, 표시 범위(기준점에서 반경 약 80m) 밖의 좌표는
// 그냥 안 보이게 잘려서 "마커가 사라진" 것처럼 보인다. 박스 가장자리에 붙여서라도
// 항상 어느 방향에 있는지는 보이도록 좌표를 박스 안쪽으로 눌러 담는다.
function clampToMapBounds(px: number) {
  return Math.min(GPS_MAP_SIZE_PX - GPS_MAP_MARKER_EDGE_PADDING_PX, Math.max(GPS_MAP_MARKER_EDGE_PADDING_PX, px));
}

const STORAGE_KEYS = {
  users: "safemaint.users",
  session: "safemaint.session",
  history: "safemaint.history",
  settings: "safemaint.settings",
  workspacePrefix: "safemaint.workspace.",
};

type WorkspaceSnapshot = {
  manuals: string[];
  selectedDocumentIds: string[];
  assessmentId: string | null;
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

function getAccessToken(): string {
  return readStorage<StoredSession | null>(STORAGE_KEYS.session, null)?.accessToken ?? "";
}

async function apiErrorMessage(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (response.status === 401) return "로그인이 만료되었습니다. 다시 로그인해 주세요.";
  if (response.status === 403) return "이 위험성평가를 사용할 권한이 없습니다.";
  return payload?.detail || fallback;
}

function normalizeAssessmentResponse(payload: AssessmentResponse): AssessmentResponse {
  if (payload.checklist_items?.length) return payload;
  return {
    ...payload,
    checklist_items: payload.tbm_checklist.map((content, index) => ({
      id: null,
      sequence: index + 1,
      content,
      is_completed: false,
      completed_by_user_id: null,
      completed_at: null,
    })),
  };
}

function refersToAttachedPhoto(question: string): boolean {
  return /(이건|이게|이것|이거|저건|저게|그건|그게|뭐야|무엇|어디에\s*쓰|용도|쓰이는|사용하는|어떤\s*(부품|제품)|비슷한|같은\s*(부품|제품)|후보)/i.test(question);
}

export default function HomePage() {
  const [page, setPage] = useState<PageMode>("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");

  useEffect(() => {
    const session = readStorage<StoredSession | null>(STORAGE_KEYS.session, null);
    if (session?.accessToken) {
      setUsername(session.username);
      setDisplayName(session.displayName);
      setPage("workspace");
    } else if (session) {
      removeStorage(STORAGE_KEYS.session);
    }
  }, []);

  function handleLogin(user: LocalUser) {
    setUsername(user.username);
    setDisplayName(user.displayName);
    writeStorage<StoredSession>(STORAGE_KEYS.session, {
      username: user.username,
      displayName: user.displayName,
      accessToken: user.accessToken,
    });
    setPage("workspace");
  }

  function handleLogout() {
    const session = readStorage<StoredSession | null>(STORAGE_KEYS.session, null);
    if (session?.accessToken) {
      void fetch(`${getApiBaseUrl()}/api/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.accessToken}` },
      }).catch(() => undefined);
    }
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
      const payload = await response.json() as LoginApiResponse;
      if (
        !response.ok
        || !payload.access_token
        || !payload.user?.id
        || !payload.user.employee_number
        || !payload.user.name
      ) {
        throw new Error(payload.detail || "로그인에 실패했습니다.");
      }
      onLogin({
        id: payload.user.id,
        username: payload.user.employee_number,
        displayName: payload.user.name,
        accessToken: payload.access_token,
      });
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
  const [autoSpeak, setAutoSpeak] = useState(false);
  const autoSpeakRef = useRef(autoSpeak);
  const [manuals, setManuals] = useState<string[]>([]);
  const [userDocuments, setUserDocuments] = useState<UserDocumentSummary[]>([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [manualStatus, setManualStatus] = useState("");
  const [sitePhotoName, setSitePhotoName] = useState("");
  const [visionSummary, setVisionSummary] = useState("");
  const [visionStatus, setVisionStatus] = useState("");
  const [catalogCandidates, setCatalogCandidates] = useState<CatalogCandidate[]>([]);
  const [ppeChecks, setPpeChecks] = useState<Record<string, boolean>>({});
  const [locationStatus, setLocationStatus] = useState("위치 미확인");
  const [gpsOrigin, setGpsOrigin] = useState<{ latitude: number; longitude: number } | null>(null);
  const [gpsLivePosition, setGpsLivePosition] = useState<{ latitude: number; longitude: number } | null>(null);
  const [calibratedEquipment, setCalibratedEquipment] = useState<VirtualEquipment[]>([]);
  const [gpsResult, setGpsResult] = useState<GpsCheckResponse | null>(null);
  const [isGpsChecking, setIsGpsChecking] = useState(false);
  const [gpsSource, setGpsSource] = useState<"default" | "real">("default");
  const [gpsPermissionDenied, setGpsPermissionDenied] = useState(false);
  const [manualLatitude, setManualLatitude] = useState("37.5665");
  const [manualLongitude, setManualLongitude] = useState("126.9780");
  const gpsWatchIdRef = useRef<number | null>(null);
  const gpsOriginRef = useRef<{ latitude: number; longitude: number } | null>(null);
  const gpsOriginLockedRef = useRef(false);
  const gpsManualOverrideRef = useRef(false);
  const latestRealPositionRef = useRef<{ latitude: number; longitude: number } | null>(null);
  const [hasRealFix, setHasRealFix] = useState(false);
  const calibratedRequestIdRef = useRef(0);
  const [activeTab, setActiveTab] = useState<"summary" | "accidents" | "evidence" | "tbm">("summary");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [form, setForm] = useState(initialForm);
  const [chatWorkContext, setChatWorkContext] = useState<typeof initialForm | null>(null);
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [savedAssessmentId, setSavedAssessmentId] = useState<string | null>(null);
  const [assessmentNotice, setAssessmentNotice] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSavingAssessment, setIsSavingAssessment] = useState(false);
  const [pendingChecklistItemIds, setPendingChecklistItemIds] = useState<Set<string>>(new Set());
  const [checklistError, setChecklistError] = useState("");
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [isVisionLoading, setIsVisionLoading] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const speechAbortRef = useRef<AbortController | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);
  const [error, setError] = useState("");
  const [workspaceRestored, setWorkspaceRestored] = useState(false);
  const assessmentDrawerRef = useRef<HTMLDetailsElement | null>(null);
  const myDocumentsInitializedRef = useRef(false);

  const refreshMyDocuments = useCallback(async () => {
    const token = getAccessToken();
    if (!token) return;
    const response = await fetch(`${getApiBaseUrl()}/api/v1/documents/mine`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) return;
    const documents = await response.json() as UserDocumentSummary[];
    const availableIds = new Set(documents.map((document) => document.document_id));
    const isFirstLoad = !myDocumentsInitializedRef.current;
    myDocumentsInitializedRef.current = true;
    setUserDocuments(documents);
    setManuals(documents.map((document) => document.original_filename));
    setSelectedDocumentIds((current) => {
      const availableSelection = current.filter((id) => availableIds.has(id));
      return isFirstLoad && availableSelection.length === 0
        ? documents.map((document) => document.document_id)
        : availableSelection;
    });

    if (documents.some((document) => ["pending", "processing"].includes(document.status))) {
      setManualStatus("업로드 완료 · PDF를 처리 중이며 아직 일반 RAG 검색에는 사용할 수 없습니다.");
    } else if (documents.some((document) => document.status === "review_required")) {
      setManualStatus("PDF 처리 완료 · 관리자 승인 대기 중입니다. 승인 전에는 업로더의 선택 미리보기만 가능합니다.");
    } else if (documents.some((document) => document.status === "failed")) {
      setManualStatus("처리에 실패한 문서가 있습니다. 문서별 실패 사유를 확인해 주세요.");
    } else if (documents.some((document) => document.status === "active")) {
      setManualStatus("승인된 매뉴얼을 DB에서 불러왔습니다 · 일반 RAG 검색에 사용할 수 있습니다.");
    } else if (documents.length > 0) {
      setManualStatus("등록 문서를 DB에서 불러왔습니다. 문서별 처리 상태를 확인해 주세요.");
    }
  }, []);

  useEffect(() => {
    const settings = readStorage<{ volume: number; fontSize: FontSize; autoSpeak?: boolean }>(STORAGE_KEYS.settings, { volume: 70, fontSize: "medium", autoSpeak: false });
    setVolume(settings.volume);
    setFontSize(settings.fontSize);
    setAutoSpeak(settings.autoSpeak ?? false);
  }, []);

  useEffect(() => {
    const saved = readStorage<WorkspaceSnapshot | null>(`${STORAGE_KEYS.workspacePrefix}${username}`, null);
    if (saved) {
      setManuals(saved.manuals ?? []);
      setSelectedDocumentIds(saved.selectedDocumentIds ?? []);
      setSavedAssessmentId(saved.assessmentId ?? null);
    }
    setWorkspaceRestored(true);
  }, [username]);

  useEffect(() => {
    if (!workspaceRestored) return;
    writeStorage(`${STORAGE_KEYS.workspacePrefix}${username}`, {
      manuals,
      selectedDocumentIds,
      assessmentId: savedAssessmentId,
    } satisfies WorkspaceSnapshot);
  }, [manuals, savedAssessmentId, selectedDocumentIds, username, workspaceRestored]);

  useEffect(() => {
    if (!workspaceRestored || manuals.length > 0 || selectedDocumentIds.length === 0) return;
    setSelectedDocumentIds([]);
  }, [manuals.length, selectedDocumentIds.length, workspaceRestored]);

  useEffect(() => {
    if (!workspaceRestored || !savedAssessmentId || !username) return;
    const token = getAccessToken();
    if (!token) {
      setSavedAssessmentId(null);
      return;
    }
    let cancelled = false;

    void (async () => {
      try {
        const response = await fetch(
          `${getApiBaseUrl()}/api/v1/assessments/${savedAssessmentId}`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (!response.ok) {
          const message = await apiErrorMessage(response, "저장된 위험성평가를 불러오지 못했습니다.");
          if (!cancelled) {
            setChecklistError(message);
            if ([401, 403, 404].includes(response.status)) {
              setSavedAssessmentId(null);
              setResult(null);
            }
          }
          return;
        }
        const payload = normalizeAssessmentResponse(await response.json() as AssessmentResponse);
        if (!cancelled) {
          setResult(payload);
          setChecklistError("");
        }
      } catch {
        if (!cancelled) {
          setChecklistError("저장된 위험성평가를 복원하려면 백엔드 연결을 확인해 주세요.");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [savedAssessmentId, username, workspaceRestored]);

  useEffect(() => {
    if (!workspaceRestored || !username) return;
    void refreshMyDocuments().catch(() => {
      // Keep the local snapshot when the backend is temporarily unavailable.
    });
  }, [refreshMyDocuments, username, workspaceRestored]);

  useEffect(() => {
    if (typeof window !== "undefined") writeStorage(STORAGE_KEYS.settings, { volume, fontSize, autoSpeak });
  }, [volume, fontSize, autoSpeak]);

  useEffect(() => {
    autoSpeakRef.current = autoSpeak;
  }, [autoSpeak]);

  useEffect(() => {
    // 실제 GPS 권한/응답을 기다리지 않고, 기본 좌표로 즉시 한 번 확인해 화면에
    // "자동으로 위치가 잡혀 있는" 상태를 바로 보여준다. 실제 위치 추적이 성공하면
    // 아래 효과가 이어서 이 값을 진짜 위치로 갱신하고, 화면에 어느 쪽인지 표시한다.
    recalibrateManualLocation(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.geolocation) return;

    // getCurrentPosition을 한 번만 부르고 성공했을 때만 watchPosition을 시작하면,
    // 이 최초 시도가 실패하는 PC에서는 이후 실제로 위치가 잡혀도(예: 개발자도구
    // Sensors로 위치를 바꾸는 경우 포함) 영원히 감지되지 않는다. 그래서 처음부터
    // watchPosition 하나로 계속 감시하면서, 오는 업데이트를 그때그때 반영한다.
    // gpsOriginRef는 "지금 기준점이 무엇인가"를 항상 최신으로 들고 있어서, 수동으로
    // 위치를 다시 잡은 뒤에도 이어지는 실제 위치 변화가 그 기준점 대비로 계속 반영된다.
    const watchId = navigator.geolocation.watchPosition(
      (position) => {
        setGpsPermissionDenied(false);
        const current = { latitude: position.coords.latitude, longitude: position.coords.longitude };
        // override 중에도(수동 확인 결과를 화면에 띄워둔 동안에도) 최신 실제 위치는
        // 계속 기록해 둔다. "실제 위치로 돌아가기"를 누르면 다음 업데이트를 기다릴
        // 필요 없이 이 값을 바로 보여줄 수 있다.
        latestRealPositionRef.current = current;
        setHasRealFix(true);
        if (!gpsOriginLockedRef.current) {
          // 실제 위치가 처음 잡히는 순간으로, "이 위치로 다시 보정"과 완전히 같은
          // 경로(recalibrateTo)로 기준점을 실제 위치로 승격한다. 이후 코드는 이미
          // 여기서 다 처리됐으므로 더 실행할 게 없다.
          recalibrateTo(current, { lockOrigin: true, source: "real" });
          return;
        }
        // "이 위치로 확인"(수동 1회 확인) 직후에는, 뒤이어 들어오는 실제 위치
        // 업데이트가 화면에 띄워둔 수동 확인 결과를 조용히 덮어쓰지 않도록 건너뛴다.
        // "실제 위치로 돌아가기"나 "이 위치로 다시 보정"이 이 override를 해제하므로
        // 그 이후엔 다시 실시간 반영된다.
        if (gpsManualOverrideRef.current) return;
        // 기준점(설비 배치)을 옮기는 것과 별개로, 이 결과가 "실제 위치"에서 온
        // 것이라는 표시는 실제 위치 업데이트가 올 때마다 매번 갱신한다.
        setGpsSource("real");
        setGpsLivePosition(current);
        void checkLocation(current.latitude, current.longitude, gpsOriginRef.current ?? current);
      },
      (watchError) => {
        // 권한 차단은 조용히 넘기면 사용자가 원인을 알 수 없으므로 명확히 표시한다.
        // 그 외(시간 초과·신호 약화 등)는 흔한 일이므로 마지막 상태를 그대로 유지한다.
        if (watchError.code === watchError.PERMISSION_DENIED) setGpsPermissionDenied(true);
      },
      { enableHighAccuracy: false, maximumAge: 10_000, timeout: 20_000 },
    );
    gpsWatchIdRef.current = watchId;

    return () => navigator.geolocation.clearWatch(watchId);
  }, []);

  useEffect(() => () => {
    speechAbortRef.current?.abort();
    for (const source of audioSourcesRef.current) {
      source.onended = null;
      try { source.stop(); } catch { /* already stopped */ }
      source.disconnect();
    }
    audioSourcesRef.current = [];
    void audioContextRef.current?.close();
  }, []);

  useEffect(() => () => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
      recorder.stream.getTracks().forEach((track) => track.stop());
    }
  }, []);

  const fontClass = useMemo(() => `font-${fontSize}`, [fontSize]);
  const highestRisk = result?.hazards.some((hazard) => hazard.risk_level === "high") ? "high" : result?.hazards.some((hazard) => hazard.risk_level === "medium") ? "medium" : result ? "low" : "pending";
  const accidentTypes = result ? Array.from(new Set(result.hazards.map((hazard) => hazard.accident_type))) : [];

  const gpsMapCenter = gpsOrigin ? { lat: gpsOrigin.latitude, lon: gpsOrigin.longitude } : null;
  const gpsLiveOffset =
    gpsMapCenter && gpsLivePosition
      ? metersOffsetFromCenter(gpsMapCenter, gpsLivePosition.latitude, gpsLivePosition.longitude)
      : null;
  const gpsLiveDistanceM = gpsLiveOffset ? Math.round(Math.hypot(gpsLiveOffset.x, gpsLiveOffset.y)) : 0;
  // 지도 박스가 실제로 표시하는 반경(대략 GPS_MAP_SIZE_PX/2 ÷ GPS_MAP_SCALE_PX_PER_M, m
  // 단위)보다 멀면 마커가 박스 밖으로 밀려서 overflow:hidden에 잘려 안 보이게 된다.
  const gpsLiveIsOffMap =
    gpsLiveOffset !== null &&
    (Math.abs(gpsLiveOffset.x) * GPS_MAP_SCALE_PX_PER_M > GPS_MAP_SIZE_PX / 2 - GPS_MAP_MARKER_EDGE_PADDING_PX ||
      Math.abs(gpsLiveOffset.y) * GPS_MAP_SCALE_PX_PER_M > GPS_MAP_SIZE_PX / 2 - GPS_MAP_MARKER_EDGE_PADDING_PX);

  function saveHistory(questionText: string, summary: string, riskLabel: string) {
    const current = readStorage<HistoryItem[]>(STORAGE_KEYS.history, []);
    const next: HistoryItem = {
      id: crypto.randomUUID(), username, createdAt: new Date().toLocaleString("ko-KR"),
      question: questionText, summary, riskLabel,
    };
    writeStorage(STORAGE_KEYS.history, [...current, next]);
  }

  function assessmentPayload() {
    return {
      ...form,
      manufacturer: form.manufacturer || null,
      energy_sources: form.energy_source ? [form.energy_source] : [],
    };
  }

  async function handleAssessment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setError("");
    setChecklistError("");
    setAssessmentNotice("");
    try {
      const token = getAccessToken();
      if (!token) throw new Error("위험성평가를 만들려면 먼저 로그인해 주세요.");
      const response = await fetch(`${getApiBaseUrl()}/api/v1/assessments/preview`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(assessmentPayload()),
      });
      if (!response.ok) {
        throw new Error(await apiErrorMessage(response, "분석 요청에 실패했습니다. 백엔드 실행 상태를 확인해 주세요."));
      }
      const payload = normalizeAssessmentResponse(await response.json() as AssessmentResponse);
      setResult(payload);
      setChatWorkContext({ ...form });
      setSavedAssessmentId(null);
      setPendingChecklistItemIds(new Set());
      const highest = payload.hazards.some((hazard) => hazard.risk_level === "high") ? "높음" : payload.hazards.some((hazard) => hazard.risk_level === "medium") ? "보통" : "낮음";
      saveHistory(form.description, `위험요인 ${payload.hazards.length}건, TBM 체크리스트 ${payload.tbm_checklist.length}건`, highest);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "알 수 없는 오류가 발생했습니다.");
    } finally {
      setIsLoading(false);
    }
  }

  async function saveAssessment() {
    if (!result || isSavingAssessment) return;
    setIsSavingAssessment(true);
    setChecklistError("");
    try {
      const token = getAccessToken();
      if (!token) throw new Error("위험성평가를 저장하려면 다시 로그인해 주세요.");
      const response = await fetch(`${getApiBaseUrl()}/api/v1/assessments`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(assessmentPayload()),
      });
      if (!response.ok) {
        throw new Error(await apiErrorMessage(response, "위험성평가를 DB에 저장하지 못했습니다."));
      }
      const payload = normalizeAssessmentResponse(await response.json() as AssessmentResponse);
      if (payload.checklist_items.some((item) => !item.id)) {
        throw new Error("저장 응답에 체크리스트 식별자가 없습니다. 백엔드 버전을 확인해 주세요.");
      }
      setResult(payload);
      setSavedAssessmentId(payload.assessment_id);
      setPendingChecklistItemIds(new Set());
      setChecklistError("");
    } catch (requestError) {
      setChecklistError(requestError instanceof Error ? requestError.message : "위험성평가 저장 중 오류가 발생했습니다.");
    } finally {
      setIsSavingAssessment(false);
    }
  }

  async function updateChecklistItem(item: ChecklistItemResponse, isCompleted: boolean) {
    if (!savedAssessmentId || !item.id || pendingChecklistItemIds.has(item.id)) return;
    const itemId = item.id;
    setChecklistError("");
    setPendingChecklistItemIds((current) => new Set(current).add(itemId));
    try {
      const token = getAccessToken();
      if (!token) throw new Error("체크 상태를 저장하려면 다시 로그인해 주세요.");
      const response = await fetch(
        `${getApiBaseUrl()}/api/v1/assessments/${savedAssessmentId}/checklist-items/${itemId}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ is_completed: isCompleted }),
        },
      );
      if (!response.ok) {
        const message = await apiErrorMessage(response, "체크 상태를 저장하지 못했습니다.");
        if ([401, 403, 404].includes(response.status)) {
          setSavedAssessmentId(null);
          setResult(null);
        }
        throw new Error(message);
      }
      const updated = await response.json() as ChecklistItemUpdateResponse;
      setResult((current) => current ? {
        ...current,
        checklist_items: current.checklist_items.map((currentItem) => (
          currentItem.id === updated.id ? updated : currentItem
        )),
      } : current);
    } catch (requestError) {
      setChecklistError(requestError instanceof Error ? requestError.message : "체크 상태 저장 중 오류가 발생했습니다.");
    } finally {
      setPendingChecklistItemIds((current) => {
        const next = new Set(current);
        next.delete(itemId);
        return next;
      });
    }
  }

  async function sendChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedQuestion = question.trim();
    if (!submittedQuestion || isChatLoading || isVisionLoading) return;
    const candidatesForAnswer = refersToAttachedPhoto(submittedQuestion) ? catalogCandidates : [];

    // Create the AudioContext synchronously within this user-gesture handler so
    // browsers don't block autoplay once the answer arrives after the awaits below.
    if (autoSpeak && !audioContextRef.current) {
      audioContextRef.current = new AudioContext();
    }

    setQuestion("");
    setIsChatLoading(true);
    setError("");
    setMessages((current) => [...current, { role: "user", text: submittedQuestion }]);

    try {
      const token = getAccessToken();
      const response = await fetch(`${getApiBaseUrl()}/api/v1/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          question: submittedQuestion,
          context: {
            site_name: chatWorkContext?.site_name || null,
            equipment_name: chatWorkContext?.equipment_name || null,
            manufacturer: chatWorkContext?.manufacturer || null,
            model_number: chatWorkContext?.model_number || null,
            component_name: chatWorkContext?.component_name || null,
            task_type: chatWorkContext?.task_type || null,
            energy_source: chatWorkContext?.energy_source || null,
            task_description: chatWorkContext?.description || null,
            visual_summary: visionSummary || null,
            selected_document_ids: selectedDocumentIds,
          },
        }),
      });
      const payload = (await response.json()) as ChatResponse & { detail?: string };
      if (!response.ok || !payload.answer) {
        throw new Error(payload.detail || "안전자료 검색에 실패했습니다.");
      }
      setMessages((current) => [
        ...current,
        {
          role: "ai",
          text: payload.answer,
          sourceQuestion: submittedQuestion,
          answerType: payload.answer_type,
          structuredAnswer: payload.structured_answer,
          checklistItems: payload.checklist_items,
          clarificationQuestion: payload.clarification_question,
          sources: payload.sources,
          retrievalMode: payload.retrieval_mode,
          generationMode: payload.generation_mode,
          model: payload.model,
          warning: payload.warning,
          accidentClassification: payload.accident_classification,
          catalogCandidates: candidatesForAnswer,
        },
      ]);
      if (!visionSummary) {
        saveHistory(
          submittedQuestion,
          `${payload.answer.slice(0, 180)}${payload.answer.length > 180 ? "…" : ""}`,
          "검토 필요",
        );
      }
      if (autoSpeakRef.current) {
        void playSpeech(payload.answer);
      }
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "백엔드에 연결할 수 없습니다.";
      setMessages((current) => [...current, { role: "ai", text: message, warning: "검색 결과를 생성하지 못했습니다." }]);
      setError(message);
    } finally {
      setIsChatLoading(false);
    }
  }

  function updateField(field: keyof typeof initialForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
    setChatWorkContext(null);
  }

  function prepareAssessmentFromChat(sourceQuestion: string) {
    const importedDescription = sourceQuestion.trim().slice(0, 2000);
    setForm((current) => ({
      ...current,
      task_type: current.task_type || importedDescription.slice(0, 100),
      description: importedDescription || current.description,
    }));
    setChatWorkContext(null);
    setResult(null);
    setSavedAssessmentId(null);
    setPendingChecklistItemIds(new Set());
    setChecklistError("");
    setError("");
    setActiveTab("tbm");
    setAssessmentNotice(
      "채팅 질문을 작업 설명으로 가져왔습니다. 사업장·설비·작업정보를 확인하고 초안을 만든 뒤 저장해 주세요. 채팅 체크리스트는 안전 검증을 위해 자동 저장되지 않습니다.",
    );

    const drawer = assessmentDrawerRef.current;
    if (drawer) {
      drawer.open = true;
      drawer.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  async function addManuals(files: FileList | null) {
    if (!files) return;
    const token = getAccessToken();
    setManualStatus("문서를 등록하고 로컬 이미지 인덱스를 생성하는 중...");
    let uploadedCount = 0;
    const uploadFailures: string[] = [];
    const visionPending: string[] = [];

    for (const file of Array.from(files)) {
      try {
        const uploadBody = new FormData();
        uploadBody.append("file", file);
        uploadBody.append("product_type", form.component_name || "미분류 설비");
        uploadBody.append("model_name", form.model_number || form.equipment_name || "미지정 모델");
        uploadBody.append("manufacturer", form.manufacturer || "미지정 제조사");
        uploadBody.append("access_level", "restricted");
        const uploadResponse = await fetch(`${getApiBaseUrl()}/api/v1/documents/upload`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: uploadBody,
        });
        const uploadPayload = await uploadResponse.json().catch(() => null) as { document_id?: string; detail?: string } | null;
        if (!uploadResponse.ok || !uploadPayload?.document_id) throw new Error(uploadPayload?.detail || `${file.name} 문서 등록 실패`);

        uploadedCount += 1;
        setManuals((current) => Array.from(new Set([...current, file.name])));
        setSelectedDocumentIds((current) => Array.from(new Set([...current, uploadPayload.document_id!])));

        const indexBody = new FormData();
        indexBody.append("document_id", uploadPayload.document_id);
        try {
          const indexResponse = await fetch(`${getApiBaseUrl()}/api/v1/vision/catalog/index`, {
            method: "POST",
            headers: { Authorization: `Bearer ${token}` },
            body: indexBody,
          });
          const indexPayload = await indexResponse.json().catch(() => null) as { document_id?: string; detail?: string } | null;
          if (!indexResponse.ok || indexPayload?.document_id !== uploadPayload.document_id) {
            visionPending.push(file.name);
          }
        } catch {
          visionPending.push(file.name);
        }
      } catch (requestError) {
        uploadFailures.push(requestError instanceof Error ? requestError.message : `${file.name} 문서 등록 실패`);
      }
    }

    if (uploadedCount > 0) {
      await refreshMyDocuments().catch(() => undefined);
    }

    if (uploadedCount > 0 && uploadFailures.length === 0 && visionPending.length === 0) {
      setManualStatus("업로드 완료 · PDF 처리 후 관리자 승인이 필요합니다. 승인 전에는 일반 RAG 검색에 포함되지 않습니다.");
    } else if (uploadedCount > 0 && uploadFailures.length === 0) {
      setManualStatus("업로드 완료 · 관리자 승인과 이미지 인덱싱이 필요합니다. 로컬 비전 서비스 상태를 확인해 주세요.");
    } else if (uploadedCount > 0) {
      setManualStatus(`${uploadedCount}개 문서 등록 완료 · 일부 실패: ${uploadFailures.join(" · ")}`);
    } else {
      setManualStatus(uploadFailures.join(" · ") || "문서 등록 실패");
    }
  }

  async function analyzePhoto(file: File | undefined) {
    if (!file) return;
    setIsVisionLoading(true);
    const token = getAccessToken();
    setSitePhotoName(file.name);
    setVisionStatus("로컬 이미지 분석 중...");
    setCatalogCandidates([]);
    const body = new FormData();
    body.append("file", file);
    body.append("document_ids", JSON.stringify(selectedDocumentIds));
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/vision/catalog/match`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body,
      });
      const payload = await response.json().catch(() => null) as {
        raw_visual_description?: string;
        extracted_markdown?: string;
        catalog_candidates?: CatalogCandidate[];
        warnings?: string[];
        detail?: string;
      } | null;
      if (!response.ok) throw new Error(payload?.detail || "이미지 분석 실패");
      const candidates = payload?.catalog_candidates ?? [];
      setCatalogCandidates(candidates);
      setVisionSummary([
        candidates.length ? `카탈로그 외형 유사 후보(동일 제품 확정 아님):\n${candidates.map((item, index) => `${index + 1}. ${item.visual_category || "종류 확인 불가"}, ${item.filename} ${item.page}페이지, 유사도 ${(item.similarity * 100).toFixed(1)}%, 특징 ${item.visual_features?.join(", ") || "확인 불가"}`).join("\n")}` : "신뢰 임계값을 넘는 카탈로그 후보 없음",
        payload?.extracted_markdown ? `로컬 OCR 확인 내용:\n${payload.extracted_markdown}` : "",
        payload?.raw_visual_description ? `로컬 비전 참고 설명(각인·규격 확정 근거 아님):\n${payload.raw_visual_description}` : "",
      ].filter(Boolean).join("\n\n"));
      setVisionStatus(payload?.warnings?.length ? `분석 완료 · ${payload.warnings.join(" · ")}` : "로컬 분석 완료");
    } catch (requestError) {
      setVisionStatus(requestError instanceof Error ? requestError.message : "로컬 비전 서비스 연결 실패");
    } finally {
      setIsVisionLoading(false);
    }
  }

  async function loadCalibratedEquipment(origin: { latitude: number; longitude: number }) {
    // 마운트 시 기본 좌표 확인과 실제 GPS 최초 확인이 거의 동시에 이 함수를 호출할 수
    // 있는데, 두 요청의 응답 순서는 보장되지 않는다. 나중에 시작된 요청보다 먼저
    // 시작된(=원점이 이미 낡은) 요청의 응답이 더 늦게 와서 최신 상태를 덮어쓰는 걸
    // 막기 위해, 가장 마지막으로 시작된 요청의 결과만 반영한다.
    const requestId = ++calibratedRequestIdRef.current;
    try {
      const params = new URLSearchParams({
        origin_latitude: String(origin.latitude),
        origin_longitude: String(origin.longitude),
      });
      const response = await fetch(`${getApiBaseUrl()}/api/v1/gps/equipment?${params.toString()}`);
      if (!response.ok) return;
      const data = (await response.json()) as VirtualEquipment[];
      if (requestId !== calibratedRequestIdRef.current) return;
      setCalibratedEquipment(data);
    } catch {
      // 지도 표시용 목록을 못 불러와도 위치 추적 자체는 계속 진행
    }
  }

  async function checkLocation(
    latitude: number,
    longitude: number,
    origin: { latitude: number; longitude: number },
  ) {
    setIsGpsChecking(true);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/gps/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          latitude,
          longitude,
          radius_m: 5000,
          origin_latitude: origin.latitude,
          origin_longitude: origin.longitude,
        }),
      });
      if (!response.ok) throw new Error("위치 확인에 실패했습니다.");
      const payload = (await response.json()) as GpsCheckResponse;
      setGpsResult(payload);
      const inRange = payload.nearby.filter((item) => item.distance_m <= GPS_EQUIPMENT_RADIUS_M);
      setLocationStatus(
        inRange.length > 0
          ? `위치 추적 중 · ${inRange.map((item) => item.equipment_name).join(", ")} 근처`
          : "위치 추적 중 · 근처에 설비 없음",
      );
    } catch (requestError) {
      setLocationStatus(requestError instanceof Error ? requestError.message : "위치 확인 중 오류가 발생했습니다.");
    } finally {
      setIsGpsChecking(false);
    }
  }

  function parseManualCoordinates(): { latitude: number; longitude: number } | null {
    const latitude = Number(manualLatitude);
    const longitude = Number(manualLongitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      setLocationStatus("위도/경도를 올바르게 입력해 주세요");
      return null;
    }
    return { latitude, longitude };
  }

  // 기준점(설비 배치)을 point로 재설정하는 유일한 경로. 마운트 시 기본값 표시,
  // 실제 GPS 최초 확인, "이 위치로 다시 보정" 버튼이 모두 이 함수 하나만 거치게
  // 해서, 원점을 옮기는 로직이 여러 곳에 비슷하게 중복되며 조금씩 어긋나는 걸 막는다.
  // lockOrigin=true(기본값)는 사용자가 명시적으로 기준점을 다시 잡는 경우로,
  // 이후 실제 GPS가 잡혀도 이 기준점을 몰래 덮어쓰지 않도록 잠근다.
  // mount 시 자동 기본값 설정만 lockOrigin=false로 호출해, 실제 GPS가 처음
  // 잡히면 그쪽으로 자동 승격될 수 있게 열어둔다.
  function recalibrateTo(point: { latitude: number; longitude: number }, options: { lockOrigin: boolean; source: "default" | "real" }) {
    gpsOriginRef.current = point;
    if (options.lockOrigin) gpsOriginLockedRef.current = true;
    // 기준점을 다시 잡는 것이므로, 이 시점부터는 실제 위치 변화가 이 기준점
    // 대비로 다시 실시간 반영되도록 수동 override를 해제한다.
    gpsManualOverrideRef.current = false;
    setGpsOrigin(point);
    setGpsLivePosition(point);
    setGpsSource(options.source);
    // 입력창을 이 기준점으로 동기화해 둔다. 안 그러면 실제 GPS가 잡혀 기준점이
    // 사용자의 실제 위치로 옮겨간 뒤에도 입력창엔 옛날 기본값이 그대로 남아서,
    // "조금만 옮겨서 테스트"해도 실제로는 기준점에서 수백~수천m 떨어진 값을
    // 건드리는 셈이 되어 매번 반경 밖으로 나온다.
    setManualLatitude(point.latitude.toFixed(6));
    setManualLongitude(point.longitude.toFixed(6));
    void loadCalibratedEquipment(point);
    void checkLocation(point.latitude, point.longitude, point);
  }

  function recalibrateManualLocation(lockOrigin = true) {
    const point = parseManualCoordinates();
    if (!point) return;
    recalibrateTo(point, { lockOrigin, source: "default" });
  }

  function checkManualLocation() {
    const point = parseManualCoordinates();
    if (!point) return;
    // 수동 입력으로 확인한 결과이므로, 직전에 실제 위치로 표시돼 있었더라도
    // 지금 보여주는 결과의 출처는 "기본 테스트 좌표"로 명확히 되돌린다. 이어서
    // 들어오는 실제 위치 업데이트가 이 결과를 곧바로 덮어쓰지 않도록 잠근다.
    // (기준점은 마운트 시 recalibrateTo로 항상 먼저 설정돼 있으므로 건드리지 않는다.)
    gpsManualOverrideRef.current = true;
    setGpsSource("default");
    setGpsLivePosition(point);
    void checkLocation(point.latitude, point.longitude, gpsOriginRef.current ?? point);
  }

  // "이 위치로 확인"으로 실제 위치 반영을 잠가둔 뒤, 기준점(설비 배치)은 그대로 둔 채
  // 실시간 위치 표시만 재개한다. 기준점까지 옮기고 싶다면 recalibrateManualLocation을 쓴다.
  function switchToRealPosition() {
    gpsManualOverrideRef.current = false;
    const real = latestRealPositionRef.current;
    if (!real) {
      setLocationStatus("아직 확인된 실제 위치가 없습니다");
      return;
    }
    setGpsSource("real");
    setGpsLivePosition(real);
    // 여기서도 입력창을 실제 위치로 맞춰둬야, 이어서 "조금 옮겨서" 테스트할 때
    // 기준점 근처의 의미 있는 값에서 시작한다.
    setManualLatitude(real.latitude.toFixed(6));
    setManualLongitude(real.longitude.toFixed(6));
    void checkLocation(real.latitude, real.longitude, gpsOriginRef.current ?? real);
  }

  function stopSpeech() {
    const wasActive = audioSourcesRef.current.length > 0 || speechAbortRef.current !== null;
    speechAbortRef.current?.abort();
    speechAbortRef.current = null;
    for (const source of audioSourcesRef.current) {
      source.onended = null;
      try { source.stop(); } catch { /* already stopped */ }
      source.disconnect();
    }
    audioSourcesRef.current = [];
    setIsSpeaking(false);
    return wasActive;
  }

  async function playSpeech(text: string) {
    stopSpeech();
    setIsSpeaking(true);
    setError("");
    const controller = new AbortController();
    speechAbortRef.current = controller;
    let scheduledCount = 0;
    let streamDone = false;
    try {
      // Unlock audio playback while the triggering click/submit is still an active user gesture.
      const audioContext = audioContextRef.current ?? new AudioContext();
      audioContextRef.current = audioContext;
      await audioContext.resume();

      const response = await fetch(`${getApiBaseUrl()}/api/v1/speech/synthesize/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, speed: 1.0 }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail || "음성 안내를 생성하지 못했습니다.");
      }

      // Chunks arrive as they finish synthesizing (length-prefixed WAV frames), so
      // playback of the first sentence can start long before the rest is ready
      // instead of waiting for the entire answer to be generated.
      let nextStartTime = audioContext.currentTime;
      let buffer = new Uint8Array(0);

      // Scheduling a chunk the instant it decodes leaves zero cushion: if the next
      // chunk's synthesis is even slightly slower than this chunk's playback
      // duration, playback catches up to nextStartTime and an audible gap opens up.
      // Holding back the first couple of chunks before starting playback gives the
      // synthesis pipeline a head start so brief slowdowns don't cause audible stalls.
      const LEAD_CHUNKS = 2;
      const pendingBuffers: AudioBuffer[] = [];
      let started = false;

      const playBuffer = (audioBuffer: AudioBuffer) => {
        if (controller.signal.aborted) return;
        const source = audioContext.createBufferSource();
        const gain = audioContext.createGain();
        gain.gain.value = volume / 100;
        source.buffer = audioBuffer;
        source.connect(gain);
        gain.connect(audioContext.destination);
        const startAt = Math.max(nextStartTime, audioContext.currentTime);
        source.onended = () => {
          source.disconnect();
          gain.disconnect();
          audioSourcesRef.current = audioSourcesRef.current.filter((s) => s !== source);
          if (streamDone && audioSourcesRef.current.length === 0) setIsSpeaking(false);
        };
        audioSourcesRef.current.push(source);
        source.start(startAt);
        nextStartTime = startAt + audioBuffer.duration;
        scheduledCount += 1;
      };

      const flushPending = () => {
        started = true;
        nextStartTime = audioContext.currentTime;
        for (const audioBuffer of pendingBuffers.splice(0)) playBuffer(audioBuffer);
      };

      const scheduleFrame = async (frameBytes: Uint8Array) => {
        const audioBuffer = await audioContext.decodeAudioData(frameBytes.buffer as ArrayBuffer);
        if (controller.signal.aborted) return;
        if (!started) {
          pendingBuffers.push(audioBuffer);
          if (pendingBuffers.length >= LEAD_CHUNKS) flushPending();
          return;
        }
        playBuffer(audioBuffer);
      };

      const drainFrames = async () => {
        for (;;) {
          if (buffer.length < 4) return;
          const frameLength = new DataView(buffer.buffer, buffer.byteOffset, 4).getUint32(0);
          if (buffer.length < 4 + frameLength) return;
          const frameBytes = buffer.slice(4, 4 + frameLength);
          buffer = buffer.slice(4 + frameLength);
          await scheduleFrame(frameBytes);
        }
      };

      const reader = response.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (controller.signal.aborted) return;
        if (value) {
          const merged = new Uint8Array(buffer.length + value.length);
          merged.set(buffer);
          merged.set(value, buffer.length);
          buffer = merged;
          await drainFrames();
        }
        if (done) break;
      }
      streamDone = true;
      // Short answers may finish with fewer than LEAD_CHUNKS chunks total, in which
      // case playback never started while waiting for a lead that will never come.
      if (!started && pendingBuffers.length > 0) flushPending();
      if (scheduledCount === 0) setIsSpeaking(false);
    } catch (speechError) {
      if (controller.signal.aborted) return;
      audioSourcesRef.current = [];
      setIsSpeaking(false);
      setError(speechError instanceof Error ? speechError.message : "음성 안내 중 오류가 발생했습니다.");
    }
  }

  async function speakGuidance() {
    if (stopSpeech()) return;

    const latestAnswer = [...messages].reverse().find((message) => message.role === "ai")?.text;
    const text = latestAnswer ?? (result
      ? `현재 분석된 위험요인은 ${result.hazards.length}건입니다. ${result.hazards.map((hazard) => `${hazard.name}. ${hazard.safety_actions.join(". ")}`).join(". ")}`
      : "작업정보를 입력하고 위험성평가를 실행한 뒤 음성 안전 안내를 들을 수 있습니다.");

    await playSpeech(text);
  }

  async function toggleVoiceInput() {
    if (isRecording) {
      mediaRecorderRef.current?.stop();
      return;
    }
    if (isTranscribing) return;

    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recordedChunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recordedChunksRef.current.push(event.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setIsRecording(false);
        const blob = new Blob(recordedChunksRef.current, { type: recorder.mimeType || "audio/webm" });
        recordedChunksRef.current = [];
        if (blob.size === 0) return;

        setIsTranscribing(true);
        try {
          const formData = new FormData();
          formData.append("file", blob, "voice-input.webm");
          const response = await fetch(`${getApiBaseUrl()}/api/v1/speech/transcribe`, {
            method: "POST",
            body: formData,
          });
          const payload = (await response.json()) as { text?: string; detail?: string };
          if (!response.ok || typeof payload.text !== "string") {
            throw new Error(payload.detail || "음성 인식에 실패했습니다.");
          }
          const transcribed = payload.text.trim();
          if (transcribed) {
            setQuestion((current) => (current ? `${current} ${transcribed}` : transcribed));
          }
        } catch (transcribeError) {
          setError(transcribeError instanceof Error ? transcribeError.message : "음성 인식 중 오류가 발생했습니다.");
        } finally {
          setIsTranscribing(false);
        }
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
    } catch (mediaError) {
      setError(mediaError instanceof Error ? mediaError.message : "마이크에 접근할 수 없습니다. 브라우저 마이크 권한을 확인해 주세요.");
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
            <label>💬 채팅 답변 자동 음성 출력<input type="checkbox" checked={autoSpeak} onChange={(event) => setAutoSpeak(event.target.checked)} /></label>
            <button onClick={() => setMessages([])}>🧹 대화 초기화</button>
            <button onClick={() => {
              setManuals([]);
              setSelectedDocumentIds([]);
              setCatalogCandidates([]);
              setVisionSummary("");
              setManualStatus("매뉴얼 선택을 초기화했습니다.");
            }}>📚 매뉴얼 목록 초기화</button>
            <button className="logout-button" onClick={onLogout}>🚪 로그아웃</button>
          </aside>
        </details>
        <div><strong>SafeMaint AI</strong><span>작업 전 위험성평가 · 사고예방 · 근거 기반 안전 안내</span></div>
        <div className="user-label"><strong>{displayName}</strong> 님</div>
      </header>

      <section className="quick-toolbar" aria-label="현장 빠른 기능">
        <label className="toolbar-upload"><InterfaceIcon name="image" />현장 사진<input type="file" accept="image/*" onChange={(event) => void analyzePhoto(event.target.files?.[0])} /></label>
        <button type="button" onClick={toggleVoiceInput} disabled={isTranscribing}>
          <InterfaceIcon name={isRecording ? "stop" : "microphone"} />{isRecording ? "녹음 중지" : isTranscribing ? "인식 중..." : "음성 입력"}
        </button>
        <button type="button" onClick={speakGuidance}><InterfaceIcon name={isSpeaking ? "stop" : "speaker"} />{isSpeaking ? "음성 중지" : "음성 안내"}</button>
        <button type="button" onClick={onHistory}><InterfaceIcon name="history" />결과 기록</button>
        <span>{locationStatus}</span>
      </section>

      <details className="assessment-drawer gps-drawer">
        <summary>
          📍 가상 GPS 자동 위치 추적 열기
          <span className={isGpsChecking ? "status-pill active" : "status-pill"}>{locationStatus}</span>
          <span className="panel-tag muted">{gpsSource === "real" ? "실제 위치 사용 중" : "기본 테스트 좌표 사용 중"}</span>
        </summary>
        <section className="panel gps-map-panel" aria-label="가상 GPS 자동 위치 추적">
        <p className="muted-copy">
          브라우저가 실제 위치를 확인하면 자동으로 그 위치를 보여주고, 실패하면 기본 테스트 좌표를 사용합니다.
          아래 위경도 값을 바꿔 <strong>이 위치로 확인</strong>을 누르면 설비 배치 기준점은 그대로 둔 채 그 좌표에서의 결과만
          1회성으로 미리볼 수 있고, <strong>실제 위치로 돌아가기</strong>로 다시 실시간 위치 표시로 돌아갈 수 있습니다.
          <strong>이 위치로 다시 보정</strong>은 설비 배치 자체의 기준점을 그 좌표로 옮깁니다.
          (점선 원은 설비별 근접 판정 반경 {GPS_EQUIPMENT_RADIUS_M}m)
        </p>
        {gpsPermissionDenied && (
          <p className="error-message">
            이 브라우저에서 위치 권한이 차단되어 있어 실제 위치로 전환될 수 없습니다.
            주소창 왼쪽의 자물쇠(사이트 정보) 아이콘 → 위치 권한을 "허용"으로 바꾼 뒤 새로고침해 주세요.
          </p>
        )}
        {gpsMapCenter ? (
          <div className="gps-map" style={{ width: GPS_MAP_SIZE_PX, height: GPS_MAP_SIZE_PX }}>
            {calibratedEquipment.map((eq) => {
              const offset = metersOffsetFromCenter(gpsMapCenter, eq.latitude, eq.longitude);
              const x = GPS_MAP_SIZE_PX / 2 + offset.x * GPS_MAP_SCALE_PX_PER_M;
              const y = GPS_MAP_SIZE_PX / 2 + offset.y * GPS_MAP_SCALE_PX_PER_M;
              const diameterPx = GPS_EQUIPMENT_RADIUS_M * GPS_MAP_SCALE_PX_PER_M * 2;
              return (
                <div key={eq.equipment_code}>
                  <span className="gps-map-radius" style={{ left: x, top: y, width: diameterPx, height: diameterPx }} />
                  <span className="gps-map-dot" style={{ left: x, top: y }} title={eq.equipment_name} />
                  <span className="gps-map-label" style={{ left: x, top: y }}>{eq.equipment_name}</span>
                </div>
              );
            })}
            {gpsLivePosition && gpsLiveOffset && (() => {
              const rawLeft = GPS_MAP_SIZE_PX / 2 + gpsLiveOffset.x * GPS_MAP_SCALE_PX_PER_M;
              const rawTop = GPS_MAP_SIZE_PX / 2 + gpsLiveOffset.y * GPS_MAP_SCALE_PX_PER_M;
              return (
                <span
                  className={gpsLiveIsOffMap ? "gps-map-marker gps-map-marker-clamped" : "gps-map-marker"}
                  style={{ left: clampToMapBounds(rawLeft), top: clampToMapBounds(rawTop) }}
                  title={gpsLiveIsOffMap ? `기준점에서 약 ${gpsLiveDistanceM}m 떨어져 있어 방향만 표시됩니다` : undefined}
                >📍</span>
              );
            })()}
          </div>
        ) : (
          <p className="muted-copy">위치 권한을 허용하면 지도가 표시됩니다.</p>
        )}
        {gpsLiveIsOffMap && (
          <p className="muted-copy">
            📍 지금 확인 중인 위치는 지도에 보이는 범위(기준점에서 약 {Math.round(GPS_MAP_SIZE_PX / 2 / GPS_MAP_SCALE_PX_PER_M)}m
            이내) 밖이라, 방향만 가장자리에 표시됩니다. 실제 거리는 약 {gpsLiveDistanceM}m입니다.
          </p>
        )}
        <div className="gps-manual-input">
          <p className="muted-copy">기본 좌표로 이미 자동 확인되어 있습니다. 다른 위치를 테스트하려면 값을 바꿔서 확인해 보세요.</p>
          <div className="gps-manual-fields">
            <label>
              위도
              <input
                value={manualLatitude}
                onChange={(event) => setManualLatitude(event.target.value)}
                inputMode="decimal"
              />
            </label>
            <label>
              경도
              <input
                value={manualLongitude}
                onChange={(event) => setManualLongitude(event.target.value)}
                inputMode="decimal"
              />
            </label>
            <button type="button" onClick={checkManualLocation} disabled={isGpsChecking}>
              이 위치로 확인
            </button>
            <button type="button" onClick={switchToRealPosition} disabled={isGpsChecking || !hasRealFix}>
              실제 위치로 돌아가기
            </button>
            <button type="button" onClick={() => recalibrateManualLocation()} disabled={isGpsChecking}>
              이 위치로 다시 보정
            </button>
          </div>
        </div>
      </section>

      {gpsResult && (() => {
        const inRangeItems = gpsResult.nearby.filter((item) => item.distance_m <= GPS_EQUIPMENT_RADIUS_M);
        const nearbyOutOfRange = gpsResult.nearby.filter((item) => item.distance_m > GPS_EQUIPMENT_RADIUS_M);
        return (
          <section className="panel gps-result-panel" aria-live="polite">
            <div className="panel-heading compact-heading">
              <div><span className="section-number">GPS</span><h2>가상 GPS 안전 체크</h2></div>
              <span className="panel-tag">{inRangeItems.length > 0 ? `${inRangeItems.length}건 감지` : "감지된 설비 없음"}</span>
            </div>
            {inRangeItems.length === 0 ? (
              <p className="muted-copy">반경 {GPS_EQUIPMENT_RADIUS_M}m 이내에 설비가 없습니다.</p>
            ) : (
              inRangeItems.map((item) => (
                <div className="gps-nearby-item" key={item.equipment_code}>
                  <div className="notice">{item.site_name} · {item.equipment_name} 앞 ({item.distance_m}m)</div>
                  <div className="hazard-list">
                    {item.hazards.map((hazard) => (
                      <article className={`hazard-card ${hazard.risk_level}`} key={hazard.name}>
                        <div className="hazard-title">
                          <div><span>{hazard.accident_type}</span><h3>{hazard.name}</h3></div>
                          <strong>{levelLabel[hazard.risk_level]} · {hazard.score}점</strong>
                        </div>
                        <ul>{hazard.safety_actions.map((action) => <li key={action}>{action}</li>)}</ul>
                      </article>
                    ))}
                  </div>
                  <div className="checklist">
                    <h3>체크리스트</h3>
                    {item.checklist.map((entry) => (
                      <label key={entry}><input type="checkbox" /><span>{entry}</span></label>
                    ))}
                  </div>
                </div>
              ))
            )}
            {nearbyOutOfRange.length > 0 && (
              <p className="muted-copy">
                {nearbyOutOfRange
                  .map((item) => `${item.equipment_name}까지 ${Math.round(item.distance_m)}m`)
                  .join(" · ")}
              </p>
            )}
          </section>
        );
      })()}
      </details>

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
          <div className="ppe-checklist">
            {ppeItems.map((item) => <label className={ppeChecks[item] ? "checked" : ""} key={item}>
              <input type="checkbox" checked={Boolean(ppeChecks[item])} onChange={(event) => setPpeChecks((current) => ({ ...current, [item]: event.target.checked }))} />
              <span className="ppe-check-icon" aria-hidden="true">✓</span><b>{item}</b><small>{ppeChecks[item] ? "착용 확인" : "확인 필요"}</small>
            </label>)}
          </div>
          <p className="muted-copy">작업과 화학물질 특성에 따라 추가 보호구가 필요할 수 있습니다.</p>
        </article>
      </section>

      <section className="manual-row upgraded-manual-row">
        <label className="file-card">📄 작업 설비 매뉴얼 추가<input type="file" accept="application/pdf" multiple onChange={(event) => void addManuals(event.target.files)} /></label>
        <div><strong>{manuals.length ? `${manuals.length}개 매뉴얼 등록됨` : "등록된 매뉴얼 없음"}</strong><span>{manualStatus || (sitePhotoName ? `현장 사진: ${sitePhotoName} · ${visionStatus}` : "현장 사진 없음")}</span></div>
        <div className="document-chip-list">
          {userDocuments.length > 0
            ? userDocuments.map((document) => {
                const isSelected = selectedDocumentIds.includes(document.document_id);
                return <article className={`uploaded-document-chip ${isSelected ? "selected" : ""}`} key={document.document_version_id}>
                  <div>
                    <strong>📄 {document.original_filename}</strong>
                    <small>버전 {document.version_number} · {DOCUMENT_STATUS_LABELS[document.status]}</small>
                    {document.fallback_used && <small className="fallback-label">PyMuPDF 대체 처리됨</small>}
                    {document.processing_warning && <small className="document-chip-warning">⚠ {document.processing_warning}</small>}
                    {document.failure_reason && <small className="document-chip-warning">{document.failure_reason}</small>}
                  </div>
                  <button type="button" aria-label={`${document.original_filename} ${isSelected ? "선택 해제" : "검색에 선택"}`} onClick={() => {
                    setSelectedDocumentIds((current) => isSelected
                      ? current.filter((id) => id !== document.document_id)
                      : [...current, document.document_id]);
                    setCatalogCandidates([]);
                    setVisionSummary("");
                  }}>{isSelected ? "선택됨" : "선택"}</button>
                </article>;
              })
            : manuals.map((name, index) => <span key={`${name}-${index}`}>📄 {name}<button type="button" aria-label={`${name} 선택 해제`} onClick={() => {
                setManuals((current) => current.filter((_, itemIndex) => itemIndex !== index));
                setSelectedDocumentIds((current) => current.filter((_, itemIndex) => itemIndex !== index));
                setCatalogCandidates([]);
                setVisionSummary("");
              }}>×</button></span>)}
        </div>
      </section>

      <DocumentReviewPanel
        apiBaseUrl={getApiBaseUrl()}
        token={getAccessToken()}
        onApproved={async () => refreshMyDocuments()}
      />

      <section className="chat-stage upgraded-chat">
        <div className="chat-heading"><div><strong>SafeMaint AI 상담</strong><span>분석 결과와 등록 문서를 바탕으로 후속 질문을 입력하세요.</span></div><button type="button" onClick={() => setMessages([])}>대화 지우기</button></div>
        <div className="chat-history">
          {messages.length === 0 ? (
            <div className="chat-empty">💬 작업 내용이나 부품 관련 질문을 입력하세요.</div>
          ) : messages.map((message, index) => (
            <div className={`chat-bubble ${message.role}`} key={`${message.role}-${index}`}>
              <strong>{message.role === "user" ? "사용자" : "SafeMaint AI"}</strong>
              {message.role === "ai" && message.retrievalMode && (
                <span className={`retrieval-badge ${message.retrievalMode}`}>
                  {message.generationMode === "openai" && `${message.model ?? "OpenAI"} + `}
                  {message.generationMode === "qwen" && `${message.model ?? "Qwen"} + `}
                  {message.sources?.length ? "BGE-M3 문서 근거 검색" : "검증 근거 없음"}
                </span>
              )}
              {message.accidentClassification && (
                <div className="team-qwen-result">
                  <span>팀 Qwen LoRA 예측</span>
                  <strong>{message.accidentClassification.label}</strong>
                  <small>{message.accidentClassification.model} · {message.accidentClassification.adapter} · 실험용 분류</small>
                </div>
              )}
              {message.role === "ai"
                ? message.structuredAnswer
                  ? <StructuredChatAnswer
                      answer={message.structuredAnswer}
                      checklistItems={message.checklistItems ?? []}
                      sources={message.sources ?? []}
                      onPrepareAssessment={message.structuredAnswer.answer_type === "maintenance_guide"
                        ? () => prepareAssessmentFromChat(message.sourceQuestion ?? "")
                        : undefined}
                    />
                  : <SafetyAnswerView answer={message.text} />
                : <p className="chat-answer-text">{message.text}</p>}
              {message.catalogCandidates && message.catalogCandidates.length > 0 && (
                <div className="catalog-candidate-list">
                  <strong>사진과 유사한 카탈로그 후보</strong>
                  <div className="catalog-candidate-grid">
                    {message.catalogCandidates.map((candidate, candidateIndex) => (
                      <article className="catalog-candidate-card" key={`${candidate.document_id}-${candidate.page}-${candidate.image_index}`}>
                        <SecureCandidateImage candidate={candidate} alt={`후보 ${candidateIndex + 1}`} />
                        <div>
                          <strong>후보 {candidateIndex + 1} · {candidate.visual_category || "제품 종류 확인 불가"}</strong>
                          <span>{candidate.filename} · {candidate.page}페이지</span>
                          <span>유사도 {(candidate.similarity * 100).toFixed(1)}% · 신뢰 {candidate.confidence}</span>
                          {candidate.visual_features && candidate.visual_features.length > 0 && <p>{candidate.visual_features.join(" · ")}</p>}
                          {candidate.page_excerpt && <small>{candidate.page_excerpt}</small>}
                        </div>
                      </article>
                    ))}
                  </div>
                  <p className="catalog-candidate-caution">후보 이미지는 외형 비교용이며 동일 모델·규격을 의미하지 않습니다.</p>
                </div>
              )}
              {message.warning && <p className="chat-warning">⚠ {message.warning}</p>}
              {message.sources && message.sources.length > 0 && (
                <div className="chat-source-list">
                  <strong>검색 근거 {message.sources.length}건</strong>
                  {message.sources.map((source) => (
                    <article key={source.chunk_id} className="chat-source-card">
                      <div>
                        <span>{source.source_type}</span>
                        <span>유사도 {(source.similarity * 100).toFixed(1)}%</span>
                      </div>
                      <strong>{source.title}</strong>
                      <small className="chat-source-location">
                        {[
                          source.original_filename || source.title,
                          source.page_start
                            ? `${source.page_start}${source.page_end && source.page_end !== source.page_start ? `–${source.page_end}` : ""}페이지`
                            : source.page ? `${source.page}페이지` : "페이지 정보 없음",
                          source.section ? `섹션: ${source.section}` : null,
                          source.document_version ? `문서 버전 ${source.document_version}` : null,
                        ].filter(Boolean).join(" · ")}
                      </small>
                      <small className="chat-source-location">
                        문서 ID {source.document_id}
                        {source.document_version_id ? ` · 문서 버전 ID ${source.document_version_id}` : ""}
                      </small>
                      <p>{source.excerpt}</p>
                      {source.url && <a href={source.url} target="_blank" rel="noreferrer">원문 확인</a>}
                    </article>
                  ))}
                </div>
              )}
            </div>
          ))}
          {isChatLoading && <div className="chat-bubble ai chat-loading"><strong>SafeMaint AI</strong>안전자료를 검색하고 AI 답변을 생성하고 있습니다…</div>}
        </div>
        <form className="chat-input-row" onSubmit={sendChat}>
          <button type="button" className="icon-action" aria-label={isRecording ? "녹음 중지" : "음성 입력"} onClick={toggleVoiceInput} disabled={isTranscribing}><InterfaceIcon name={isRecording ? "stop" : "microphone"} /><span>음성</span></button>
          <label className="icon-action file-icon" aria-label="사진 첨부"><InterfaceIcon name="image" /><span>사진</span><input type="file" accept="image/*" onChange={(event) => void analyzePhoto(event.target.files?.[0])} /></label>
          <label className="icon-action file-icon" aria-label="문서 첨부"><InterfaceIcon name="paperclip" /><span>문서</span><input type="file" accept="application/pdf" multiple onChange={(event) => void addManuals(event.target.files)} /></label>
          <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={isVisionLoading ? "사진 분석이 끝나면 질문을 보낼 수 있습니다." : "예: 이건 뭐야? 어디에 쓰이는 거야?"} disabled={isChatLoading} />
          <button type="submit" disabled={isChatLoading || isVisionLoading || !question.trim()}>{isVisionLoading ? "사진 분석 중..." : isChatLoading ? "답변 생성 중..." : "전송"}</button>
        </form>
      </section>

      <details className="assessment-drawer" ref={assessmentDrawerRef}>
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
                <Field label="사업장"><input value={form.site_name} onChange={(e) => updateField("site_name", e.target.value)} placeholder="사업장명을 입력하세요" required /></Field>
                <Field label="설비명"><input value={form.equipment_name} onChange={(e) => updateField("equipment_name", e.target.value)} placeholder="설비명을 입력하세요" required /></Field>
                <Field label="제조사"><input value={form.manufacturer} onChange={(e) => updateField("manufacturer", e.target.value)} placeholder="선택 입력" /></Field>
                <Field label="모델·부품번호"><input value={form.model_number} onChange={(e) => updateField("model_number", e.target.value)} /></Field>
                <Field label="부품"><input value={form.component_name} onChange={(e) => updateField("component_name", e.target.value)} /></Field>
                <Field label="작업 종류"><input value={form.task_type} onChange={(e) => updateField("task_type", e.target.value)} placeholder="수행할 작업을 입력하세요" required /></Field>
                <Field label="주요 에너지원"><select value={form.energy_source} onChange={(e) => updateField("energy_source", e.target.value)}><option value="">미확인</option><option value="전기">전기</option><option value="기계">기계</option><option value="압력">압력</option><option value="열">열</option></select></Field>
              </div>
              <Field label="작업 설명"><textarea value={form.description} onChange={(e) => updateField("description", e.target.value)} placeholder="수행할 작업 범위와 현재 상태를 입력해 주세요." minLength={5} rows={4} required /></Field>
              {assessmentNotice && <p className="assessment-import-notice" role="status">{assessmentNotice}</p>}
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
            {!result ? <div className="empty-state"><div className="empty-icon">!</div><h3>아직 분석 결과가 없습니다.</h3><p>왼쪽 작업정보를 확인하고 초안 만들기를 실행해 주세요.</p></div> : activeTab === "summary" ? <AssessmentResult result={result} mode="hazards" /> : activeTab === "accidents" ? <SimilarAccidentPanel result={result} /> : activeTab === "evidence" ? <EvidencePanel result={result} manuals={manuals} /> : (
              <AssessmentResult
                result={result}
                mode="tbm"
                persistence={savedAssessmentId === result.assessment_id ? "saved" : "preview"}
                isSavingAssessment={isSavingAssessment}
                pendingItemIds={pendingChecklistItemIds}
                checklistError={checklistError}
                onSave={() => void saveAssessment()}
                onToggle={(item, isCompleted) => void updateChecklistItem(item, isCompleted)}
              />
            )}
          </section>
        </div>
      </details>

      <footer className="safety-footer">본 결과는 작업 전 검토를 위한 초안이며, 현장 안전관리자의 최종 확인과 승인 없이 작업을 시작할 수 없습니다.</footer>
    </main>
  );
}

function AssessmentResult({
  result,
  mode = "hazards",
  persistence = "preview",
  isSavingAssessment = false,
  pendingItemIds = new Set<string>(),
  checklistError = "",
  onSave = () => undefined,
  onToggle = () => undefined,
}: {
  result: AssessmentResponse;
  mode?: "hazards" | "tbm";
  persistence?: "preview" | "saved";
  isSavingAssessment?: boolean;
  pendingItemIds?: ReadonlySet<string>;
  checklistError?: string;
  onSave?: () => void;
  onToggle?: (item: ChecklistItemResponse, isCompleted: boolean) => void;
}) {
  const mustStop = result.hazards.some((hazard) => hazard.risk_level === "high");
  const decision = mustStop ? "작업 중지 및 안전관리자 확인 필요" : result.evidence_status === "connected" ? "안전관리자 검토 가능" : "근거 부족으로 판단 불가";

  if (mode === "tbm") return (
    <div className="result-content">
      <TbmChecklist
        result={result}
        persistence={persistence}
        isSavingAssessment={isSavingAssessment}
        pendingItemIds={pendingItemIds}
        error={checklistError}
        onSave={onSave}
        onToggle={onToggle}
      />
    </div>
  );

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
  return <div className="similar-accident-list">{types.map((type) => <article className="accident-card" key={type}><div><span>규칙 기반 분류</span><strong>{type} 사고 가능성</strong></div><p>실제 유사 사고 근거가 연결되지 않았습니다. 사고사례 검색 결과가 확보되기 전에는 구체적인 예방 절차를 표시하지 않습니다.</p></article>)}</div>;
}

function SecureCandidateImage({ candidate, alt }: { candidate: CatalogCandidate; alt: string }) {
  const [source, setSource] = useState("");

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    const token = getAccessToken();
    void fetch(`${getApiBaseUrl()}/api/v1/vision/catalog/image/${candidate.document_id}/${candidate.page}/${candidate.image_index}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((response) => {
        if (!response.ok) throw new Error("후보 이미지 로드 실패");
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (active) setSource(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [candidate.document_id, candidate.image_index, candidate.page]);

  return source ? <img src={source} alt={alt} loading="lazy" /> : <div className="catalog-image-placeholder">이미지 불러오는 중</div>;
}

type InterfaceIconName = "location" | "image" | "microphone" | "speaker" | "stop" | "history" | "paperclip";

function InterfaceIcon({ name }: { name: InterfaceIconName }) {
  const paths: Record<InterfaceIconName, React.ReactNode> = {
    location: <><path d="M12 21s6-5.1 6-11a6 6 0 1 0-12 0c0 5.9 6 11 6 11Z" /><circle cx="12" cy="10" r="2" /></>,
    image: <><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8.5" cy="9" r="1.5" /><path d="m4 17 5-5 4 4 2-2 5 5" /></>,
    microphone: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8" /></>,
    speaker: <><path d="M5 9v6h4l5 4V5L9 9H5Z" /><path d="M17 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    history: <><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8" /><path d="M4 4v4h4M12 8v5l3 2" /></>,
    paperclip: <path d="m9 17 7.5-7.5a3 3 0 0 0-4.2-4.2L4.8 12.8a5 5 0 0 0 7.1 7.1l7-7" />,
  };
  return <svg className="interface-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}
