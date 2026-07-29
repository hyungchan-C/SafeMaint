"use client";

import {
  FormEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import ChatAnswerContent from "@/components/ChatAnswerContent";
import DocumentReviewPanel from "@/components/DocumentReviewPanel";
import DocumentViewerModal, { type DocumentViewerTarget } from "@/components/DocumentViewerModal";
import InterfaceIcon from "@/components/InterfaceIcon";
import ManualManager from "@/components/ManualManager";
import NotificationCenter from "@/components/NotificationCenter";
import { ChatChecklist } from "@/components/StructuredChatAnswer";
import TbmChecklist from "@/components/TbmChecklist";
import WorkspaceHeader from "@/components/WorkspaceHeader";
import type {
  AssessmentResponse,
  AssessmentSummaryResponse,
  ChecklistItemResponse,
  ChecklistItemUpdateResponse,
} from "@/types/assessment";
import type {
  CatalogCandidate,
  ChatChecklistItem,
  ChatMessage,
  ChatResponse,
  ChatSource,
  StructuredAnswer,
} from "@/types/chat";
import type {
  DocumentProcessingProgress,
  FileProcessingProgress,
  UserDocumentSummary,
} from "@/types/documents";
import type { GpsCheckResponse, NearbyEquipmentItem, VirtualEquipment } from "@/types/gps";
import { getAccessToken, getApiBaseUrl } from "@/lib/api";

type PageMode = "login" | "workspace" | "history";
type FontSize = "small" | "medium" | "large";
type MobileWorkspaceTab = "chat" | "evidence" | "safety" | "more";

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
const DOCUMENT_PROGRESS_POLL_MS = 2000;
const DOCUMENT_PROGRESS_MAX_RETRIES = 3;
const MOBILE_WORKSPACE_TAB_ORDER: MobileWorkspaceTab[] = ["chat", "evidence", "safety", "more"];
const MOBILE_SWIPE_MIN_DISTANCE_PX = 56;
const MOBILE_SWIPE_DIRECTION_RATIO = 1.25;

function metersOffsetFromCenter(center: { lat: number; lon: number }, lat: number, lon: number) {
  return {
    x: (lon - center.lon) * metersPerDegLon(center.lat),
    y: (center.lat - lat) * METERS_PER_DEG_LAT,
  };
}

// 지도 박스는 overflow: hidden이라, 표시 범위 밖의 오프셋은 그냥 안 보이게 잘린다.
// 가장자리 패딩 안쪽으로 들어오는지를 기준으로 "박스 밖"인지 판단한다.
function isOffsetOffMap(offset: { x: number; y: number }) {
  return (
    Math.abs(offset.x) * GPS_MAP_SCALE_PX_PER_M > GPS_MAP_SIZE_PX / 2 - GPS_MAP_MARKER_EDGE_PADDING_PX ||
    Math.abs(offset.y) * GPS_MAP_SCALE_PX_PER_M > GPS_MAP_SIZE_PX / 2 - GPS_MAP_MARKER_EDGE_PADDING_PX
  );
}

// 실제 GPS가 아직 없을 때 사용하는 기본 "현재 위치"(테스트 좌표의 시작값이자, 지도에
// 보여줄 기준 앵커). 실제 위치가 처음 잡히면 이 앵커는 그 실제 위치로 대체된다
// (watchPosition의 최초 fix 처리 참고).
const GPS_DEFAULT_ANCHOR = { latitude: 37.5665, longitude: 126.978 };
// 설비 배치(A공장 설비 3대: CV-203/PNL-01/WLD-05, 반경 30m 안에 서로 모여 있음)는 항상
// 이 앵커(현재 위치)로부터 아래 간격만큼 떨어진 곳에 놓인다. 모든 설비 반경(30m) 밖이면서
// (최소 약 45m) 동시에 지도가 실제로 보여주는 범위(약 70m, GPS_MAP_SIZE_PX/
// GPS_MAP_SCALE_PX_PER_M 기준) 안(최대 약 53m)에 들어오도록 골랐다. 그래야 설비가 현재
// 위치(실제 위치 포함) 바로 앞이 아니라 "조금 떨어진 곳"에 있으면서도 지도에 보이고,
// 위경도 값을 옮기거나 실제로 걸어가야("이동해야") 반경 안으로 들어온다.
const EQUIPMENT_ORIGIN_GAP_EAST_M = -35;
const EQUIPMENT_ORIGIN_GAP_NORTH_M = 28;

function offsetPointByMeters(origin: { latitude: number; longitude: number }, eastM: number, northM: number) {
  return {
    latitude: origin.latitude + northM / METERS_PER_DEG_LAT,
    longitude: origin.longitude + eastM / metersPerDegLon(origin.latitude),
  };
}

function deriveEquipmentOrigin(anchor: { latitude: number; longitude: number }) {
  return offsetPointByMeters(anchor, EQUIPMENT_ORIGIN_GAP_EAST_M, EQUIPMENT_ORIGIN_GAP_NORTH_M);
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

// 화면에 표시되는 답변 원문과 TTS가 읽는 내용이 어긋나지 않도록 같은 text를 사용한다.
// 유지보수 안내만 작업 승인이 아니라는 고정 문구를 먼저 재생한다.
const MAINTENANCE_TTS_DISCLAIMER =
  "이 안내는 작업 승인이 아닙니다. 안전관리자의 최종 확인 전에는 작업을 시작하지 마세요.";

function buildSpeechText(message: { text: string; structuredAnswer?: StructuredAnswer | null }): string {
  const answer = message.structuredAnswer;
  if (answer?.answer_type === "clarification_required") {
    return [answer.question, ...answer.options].filter(Boolean).join(". ");
  }
  if (answer?.answer_type === "no_evidence") {
    return [answer.message, answer.work_safety_notice].filter(Boolean).join(" ");
  }
  if (answer?.answer_type === "maintenance_guide") {
    return [MAINTENANCE_TTS_DISCLAIMER, message.text].filter(Boolean).join(" ");
  }
  return message.text;
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
  const normalized = question.replace(/\s+/g, "");
  return /(이것|이거|이게|이건|저것|저거|그것|그거|사진|이미지|방금|첨부|보낸|찍은|뭐야|무엇|어디에쓰|용도|어떤(?:부품|제품)|비슷한(?:부품|제품)|후보)/i.test(normalized);
}

function formatElapsedTime(elapsedMs: number): string {
  const totalSeconds = Math.max(1, Math.round(elapsedMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [
    hours ? `${hours}시간` : "",
    minutes ? `${minutes}분` : "",
    `${seconds}초`,
  ].filter(Boolean).join(" ");
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
        <img
          className="auth-brand-logo"
          src="/brand/safemaint-logo-horizontal-color.svg"
          alt="SafeMaint AI"
          width="320"
          height="56"
        />
        <h1>현장 안전 작업 지원</h1>
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

// 페이지 전환(page state)으로 보여주면 그 사이 WorkspaceScreen 전체가 언마운트돼
// 진행 중이던 채팅(messages)이 사라진다("체크리스트 확인하고 나면 채팅 없어지더라").
// 그래서 별도 화면이 아니라 워크스페이스 안의 접이식 패널로 두고, <details>는 접어도
// 안의 컴포넌트가 언마운트되지 않으므로 채팅 상태가 그대로 유지된다.
function AssessmentsPanel() {
  const [items, setItems] = useState<AssessmentSummaryResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AssessmentResponse | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  // 이 패널은 (채팅이 사라지는 버그를 막기 위해) 워크스페이스 화면 안에 계속
  // 마운트돼 있어서, 마운트 시 한 번만 불러오면 그 이후 채팅/GPS에서 새로 저장한
  // 체크리스트가 목록에 반영되지 않는다. 그래서 "새로고침" 버튼으로 다시 불러올 수
  // 있게 fetch 로직을 재사용 가능한 함수로 뺀다.
  async function loadItems() {
    setIsLoading(true);
    setError("");
    try {
      const token = getAccessToken();
      const response = await fetch(`${getApiBaseUrl()}/api/v1/assessments`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new Error(await apiErrorMessage(response, "목록을 불러오지 못했습니다."));
      setItems(await response.json() as AssessmentSummaryResponse[]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "목록을 불러오지 못했습니다.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadItems();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function toggleExpand(assessmentId: string) {
    if (expandedId === assessmentId) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(assessmentId);
    setDetail(null);
    setIsLoadingDetail(true);
    try {
      const token = getAccessToken();
      const response = await fetch(`${getApiBaseUrl()}/api/v1/assessments/${assessmentId}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new Error(await apiErrorMessage(response, "상세 내용을 불러오지 못했습니다."));
      setDetail(await response.json() as AssessmentResponse);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "상세 내용을 불러오지 못했습니다.");
    } finally {
      setIsLoadingDetail(false);
    }
  }

  function renderCard(item: AssessmentSummaryResponse) {
    return (
      <article className="history-card assessment-card" key={item.assessment_id}>
        <div><strong>{item.equipment_name} · {item.task_type}</strong><span>{new Date(item.created_at).toLocaleString()}</span></div>
        <p>{item.description}</p>
        <div className="assessment-card-meta">
          <span className={`status-pill ${item.checklist_total > 0 && item.checklist_completed === item.checklist_total ? "complete" : "incomplete"}`}>
            {item.checklist_completed}/{item.checklist_total} 완료
          </span>
          {item.created_by_name && <span className="panel-tag muted">작성자: {item.created_by_name}</span>}
          <button type="button" className="secondary-button compact" onClick={() => void toggleExpand(item.assessment_id)}>
            {expandedId === item.assessment_id ? "접기" : "체크리스트 보기"}
          </button>
        </div>
        {expandedId === item.assessment_id && (
          isLoadingDetail ? <p className="muted-copy">불러오는 중...</p> : detail && (
            <div className="chat-checklist-items">
              {/* 조회 전용 패널이라 체크박스는 항상 비활성화한다. 다른 사람(특히
                  admin이 열람하는 남의 기록)의 완료 상태를 여기서 실수로 바꾸지
                  않도록, 체크 상태를 바꾸는 액션은 이 패널에 두지 않는다. */}
              {detail.checklist_items.map((checklistItem) => (
                <label className={checklistItem.is_completed ? "checked" : ""} key={checklistItem.id ?? checklistItem.sequence}>
                  <input type="checkbox" checked={checklistItem.is_completed} disabled readOnly />
                  <span>{checklistItem.sequence}. {checklistItem.content}</span>
                </label>
              ))}
            </div>
          )
        )}
      </article>
    );
  }

  // GPS 근접 안내(정기 순찰 점검)와 채팅 상담 체크리스트는 근거·목적이 서로 달라서
  // (사용자 요청: "정기 순찰 점검이면 분리해서 따로 모아서 표시") task_type 기준으로
  // 나눠서 보여준다.
  const patrolItems = items.filter((item) => item.task_type === "정기 순찰 점검");
  const chatItems = items.filter((item) => item.task_type !== "정기 순찰 점검");

  return (
    <div className="assessment-groups">
      <div className="assessment-groups-toolbar">
        <button type="button" className="secondary-button compact" onClick={() => void loadItems()} disabled={isLoading}>
          {isLoading ? "불러오는 중..." : "새로고침"}
        </button>
      </div>
      {error && <p className="error-message">{error}</p>}
      {!isLoading && items.length === 0 && (
        <p className="muted-copy">
          저장된 위험성평가가 없습니다. 채팅 답변의 TBM 체크리스트나 GPS 근접 안내의 체크리스트에서
          &quot;이 체크리스트 저장&quot;을 누르면 여기에 표시됩니다.
        </p>
      )}
      {patrolItems.length > 0 && (
        <div className="assessment-group">
          <h3>정기 순찰 점검 (GPS 근접 안내)</h3>
          <div className="history-list">{patrolItems.map(renderCard)}</div>
        </div>
      )}
      {chatItems.length > 0 && (
        <div className="assessment-group">
          <h3>채팅 상담 체크리스트</h3>
          <div className="history-list">{chatItems.map(renderCard)}</div>
        </div>
      )}
    </div>
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
  const assessmentsDrawerRef = useRef<HTMLDetailsElement>(null);
  const [assessmentsOpenCount, setAssessmentsOpenCount] = useState(0);
  function openAssessmentsDrawer() {
    const drawer = assessmentsDrawerRef.current;
    if (!drawer) return;
    drawer.open = true;
    drawer.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const [volume, setVolume] = useState(70);
  const [fontSize, setFontSize] = useState<FontSize>("medium");
  const [autoSpeak, setAutoSpeak] = useState(false);
  const autoSpeakRef = useRef(autoSpeak);
  const [manuals, setManuals] = useState<string[]>([]);
  const [userDocuments, setUserDocuments] = useState<UserDocumentSummary[]>([]);
  const [processingProgress, setProcessingProgress] = useState<Record<string, FileProcessingProgress>>({});
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [isDocumentPanelOpen, setIsDocumentPanelOpen] = useState(false);
  const [isStatusPanelOpen, setIsStatusPanelOpen] = useState(false);
  const [isSafetyPanelOpen, setIsSafetyPanelOpen] = useState(false);
  const [mobileWorkspaceTab, setMobileWorkspaceTab] = useState<MobileWorkspaceTab>("chat");
  const mobileSwipeRef = useRef<{
    active: boolean;
    startX: number;
    startY: number;
    lastX: number;
    lastY: number;
  } | null>(null);
  const [manualStatus, setManualStatus] = useState("");
  const [sitePhotoName, setSitePhotoName] = useState("");
  const [visionSummary, setVisionSummary] = useState("");
  const [visionStatus, setVisionStatus] = useState("");
  const [visionElapsedMs, setVisionElapsedMs] = useState<number | null>(null);
  const [catalogCandidates, setCatalogCandidates] = useState<CatalogCandidate[]>([]);
  const [documentViewerTarget, setDocumentViewerTarget] = useState<DocumentViewerTarget | null>(null);
  const [visualCategories, setVisualCategories] = useState<string[]>([]);
  const [visualFeatures, setVisualFeatures] = useState<string[]>([]);
  const [ppeChecks, setPpeChecks] = useState<Record<string, boolean>>({});
  const [locationStatus, setLocationStatus] = useState("위치 미확인");
  const [gpsOrigin, setGpsOrigin] = useState<{ latitude: number; longitude: number } | null>(null);
  const [gpsLivePosition, setGpsLivePosition] = useState<{ latitude: number; longitude: number } | null>(null);
  const [calibratedEquipment, setCalibratedEquipment] = useState<VirtualEquipment[]>([]);
  const [gpsResult, setGpsResult] = useState<GpsCheckResponse | null>(null);
  const [isGpsChecking, setIsGpsChecking] = useState(false);
  // 설비별(equipment_code 기준) 순찰 체크리스트 상태. gpsResult는 위치가 바뀔 때마다
  // 통째로 새로 오므로, 이미 저장한 위험성평가 id는 별도로(설비 코드 기준) 계속 들고
  // 있어야 재저장(변경사항 저장)이 새 assessment를 또 만들지 않고 이어서 갱신된다.
  const [gpsChecklistChecked, setGpsChecklistChecked] = useState<Record<string, Set<number>>>({});
  const [gpsSavedChecklists, setGpsSavedChecklists] = useState<
    Record<string, { assessmentId: string; items: { id: string | null; is_completed: boolean }[] }>
  >({});
  const [savingGpsEquipmentCode, setSavingGpsEquipmentCode] = useState<string | null>(null);
  const [gpsSource, setGpsSource] = useState<"default" | "real">("default");
  const [gpsPermissionDenied, setGpsPermissionDenied] = useState(false);
  const [manualLatitude, setManualLatitude] = useState(GPS_DEFAULT_ANCHOR.latitude.toFixed(6));
  const [manualLongitude, setManualLongitude] = useState(GPS_DEFAULT_ANCHOR.longitude.toFixed(6));
  const gpsWatchIdRef = useRef<number | null>(null);
  const gpsOriginRef = useRef<{ latitude: number; longitude: number } | null>(null);
  const gpsManualOverrideRef = useRef(false);
  const hasPromotedRealOriginRef = useRef(false);
  const latestRealPositionRef = useRef<{ latitude: number; longitude: number } | null>(null);
  const [hasRealFix, setHasRealFix] = useState(false);
  const calibratedRequestIdRef = useRef(0);
  const [activeTab, setActiveTab] = useState<"summary" | "accidents" | "evidence">("summary");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const form = initialForm;
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [savedAssessmentId, setSavedAssessmentId] = useState<string | null>(null);
  const [isSavingAssessment, setIsSavingAssessment] = useState(false);
  const [pendingChecklistItemIds, setPendingChecklistItemIds] = useState<Set<string>>(new Set());
  const [savingChecklistIndex, setSavingChecklistIndex] = useState<number | null>(null);
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
  const visionRequestIdRef = useRef(0);
  const sitePhotoFileRef = useRef<File | null>(null);
  const [error, setError] = useState("");
  const [workspaceRestored, setWorkspaceRestored] = useState(false);
  const myDocumentsInitializedRef = useRef(false);
  const uploadXhrsRef = useRef<Map<string, XMLHttpRequest>>(new Map());
  const progressPollersRef = useRef<
    Map<string, { controller: AbortController; timer: number | null }>
  >(new Map());

  useEffect(() => {
    const desktopLayout = window.matchMedia("(min-width: 768px)");
    const syncResponsivePanels = () => {
      const shouldExpand = desktopLayout.matches;
      setIsDocumentPanelOpen(shouldExpand);
      setIsStatusPanelOpen(shouldExpand);
      setIsSafetyPanelOpen(shouldExpand);
    };
    syncResponsivePanels();
    desktopLayout.addEventListener("change", syncResponsivePanels);
    return () => desktopLayout.removeEventListener("change", syncResponsivePanels);
  }, []);

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
    setProcessingProgress((current) => {
      const next = { ...current };
      for (const document of documents) {
        const metadata = document.progress_metadata ?? {};
        const terminal = ["review_required", "active", "failed", "ocr_required"].includes(document.status);
        const ragReady = document.status === "active" && document.is_active;
        const percent = document.status === "active" || document.status === "review_required"
          ? 100
          : Math.max(0, Math.min(100, document.progress_percent ?? 15));
        next[document.document_id] = {
          client_key: document.document_id,
          document_id: document.document_id,
          document_version_id: document.document_version_id,
          filename: document.original_filename,
          status: document.status,
          stage: document.status === "active"
            ? "completed"
            : document.status === "review_required"
              ? "review_required"
              : document.processing_stage ?? "queued",
          attempt: document.processing_attempt ?? 0,
          progress_percent: percent,
          message: ragReady
            ? "승인이 완료되어 RAG 검색에 사용할 수 있습니다."
            : document.status === "review_required"
              ? "PDF 처리가 완료되었습니다. 관리자 승인이 필요합니다."
              : document.progress_message ?? "PDF 처리 대기 중",
          processed_pages: metadata.processed_pages ?? 0,
          total_pages: metadata.total_pages ?? document.page_count ?? 0,
          processed_chunks: metadata.processed_chunks ?? 0,
          total_chunks: metadata.total_chunks ?? 0,
          embedded_chunks: metadata.embedded_chunks ?? 0,
          updated_at: document.created_at,
          is_terminal: terminal,
          rag_ready: ragReady,
        };
      }
      const availableDocumentIds = new Set(documents.map((document) => document.document_id));
      for (const [key, progress] of Object.entries(next)) {
        if (progress.document_id && !availableDocumentIds.has(progress.document_id)) {
          delete next[key];
        }
      }
      return next;
    });
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

  const stopProgressPolling = useCallback((documentId: string) => {
    const poller = progressPollersRef.current.get(documentId);
    if (!poller) return;
    poller.controller.abort();
    if (poller.timer !== null) window.clearTimeout(poller.timer);
    progressPollersRef.current.delete(documentId);
  }, []);

  const startProgressPolling = useCallback((documentId: string, filename: string) => {
    if (progressPollersRef.current.has(documentId)) return;
    const poller = { controller: new AbortController(), timer: null as number | null };
    progressPollersRef.current.set(documentId, poller);
    let consecutiveFailures = 0;

    const poll = async () => {
      try {
        const token = getAccessToken();
        if (!token) {
          stopProgressPolling(documentId);
          onLogout();
          return;
        }
        const response = await fetch(
          `${getApiBaseUrl()}/api/v1/documents/${documentId}/processing-progress`,
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: poller.controller.signal,
          },
        );
        if (response.status === 401) {
          stopProgressPolling(documentId);
          onLogout();
          return;
        }
        if (!response.ok) {
          throw new Error(await apiErrorMessage(response, `${filename} 처리 상태를 확인하지 못했습니다.`));
        }
        const payload = await response.json() as DocumentProcessingProgress;
        consecutiveFailures = 0;
        setProcessingProgress((current) => {
          const previous = current[documentId];
          const sameAttempt = previous?.attempt === payload.attempt;
          return {
            ...current,
            [documentId]: {
              ...payload,
              client_key: documentId,
              progress_percent: sameAttempt
                ? Math.max(previous.progress_percent, payload.progress_percent)
                : payload.progress_percent,
            },
          };
        });
        if (payload.is_terminal) {
          stopProgressPolling(documentId);
          void refreshMyDocuments().catch(() => undefined);
          return;
        }
      } catch (requestError) {
        if (poller.controller.signal.aborted) return;
        consecutiveFailures += 1;
        const message = requestError instanceof Error
          ? requestError.message
          : `${filename} 처리 상태를 확인하지 못했습니다.`;
        setProcessingProgress((current) => {
          const previous = current[documentId];
          if (!previous) return current;
          return {
            ...current,
            [documentId]: {
              ...previous,
              message: consecutiveFailures < DOCUMENT_PROGRESS_MAX_RETRIES
                ? `상태 조회 재시도 중 (${consecutiveFailures}/${DOCUMENT_PROGRESS_MAX_RETRIES})`
                : `${message} · 문서 상태 새로고침을 눌러 다시 확인해 주세요.`,
            },
          };
        });
        if (consecutiveFailures >= DOCUMENT_PROGRESS_MAX_RETRIES) {
          stopProgressPolling(documentId);
          return;
        }
      }
      poller.timer = window.setTimeout(() => void poll(), DOCUMENT_PROGRESS_POLL_MS);
    };

    void poll();
  }, [onLogout, refreshMyDocuments, stopProgressPolling]);

  useEffect(() => {
    for (const document of userDocuments) {
      if (["pending", "processing"].includes(document.status)) {
        startProgressPolling(document.document_id, document.original_filename);
      } else {
        stopProgressPolling(document.document_id);
      }
    }
  }, [startProgressPolling, stopProgressPolling, userDocuments]);

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
    // 실제 GPS 응답을 기다리지 않고, 기본 앵커(GPS_DEFAULT_ANCHOR)를 "현재 위치"로 즉시
    // 보여준다. 설비 배치는 이 앵커에서 EQUIPMENT_ORIGIN_GAP만큼 떨어진 곳에 둬서,
    // 시작하자마자 설비 앞에 서 있는 것처럼 보이지 않게 한다. 실제 위치가 처음 잡히면
    // (watchPosition 참고) 이 앵커와 설비 배치 모두 그 실제 위치 기준으로 다시 잡힌다.
    const origin = deriveEquipmentOrigin(GPS_DEFAULT_ANCHOR);
    gpsOriginRef.current = origin;
    setGpsOrigin(origin);
    void loadCalibratedEquipment(origin);
    setGpsSource("default");
    setGpsLivePosition(GPS_DEFAULT_ANCHOR);
    void checkLocation(GPS_DEFAULT_ANCHOR.latitude, GPS_DEFAULT_ANCHOR.longitude, origin);
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
        if (!hasPromotedRealOriginRef.current) {
          // 실제 위치가 처음 잡히는 순간, 설비 배치를 기본 앵커 대신 이 실제 위치
          // 기준(EQUIPMENT_ORIGIN_GAP만큼 떨어진 곳)으로 한 번만 옮긴다. 그래야 설비가
          // 기본 좌표(예: 서울)처럼 실제 위치와 무관한 곳이 아니라 사용자 근처에 있으면서도,
          // 정확히 발밑은 아니어서 여전히 "이동해야" 반경 안으로 들어온다. 이후 GPS가
          // 흔들려도(오차로 위치가 조금씩 바뀌어도) 설비를 계속 따라 옮기지 않도록 한 번만
          // 수행한다 — 다시 옮기고 싶으면 "이 위치로 다시 보정" 버튼을 쓴다.
          hasPromotedRealOriginRef.current = true;
          const origin = deriveEquipmentOrigin(current);
          gpsOriginRef.current = origin;
          setGpsOrigin(origin);
          void loadCalibratedEquipment(origin);
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

  useEffect(() => () => {
    for (const xhr of uploadXhrsRef.current.values()) xhr.abort();
    uploadXhrsRef.current.clear();
    for (const poller of progressPollersRef.current.values()) {
      poller.controller.abort();
      if (poller.timer !== null) window.clearTimeout(poller.timer);
    }
    progressPollersRef.current.clear();
  }, []);

  const fontClass = useMemo(() => `font-${fontSize}`, [fontSize]);
  const highestRisk = result?.hazards.some((hazard) => hazard.risk_level === "high") ? "high" : result?.hazards.some((hazard) => hazard.risk_level === "medium") ? "medium" : result ? "low" : "pending";
  const latestChecklistMessageIndex = messages.findLastIndex(
    (candidate) => candidate.role === "ai" && Boolean(candidate.checklistItems?.length),
  );
  const latestChecklistMessage = latestChecklistMessageIndex >= 0 ? messages[latestChecklistMessageIndex] : null;

  function requireActiveSession(response: Response) {
    if (response.status !== 401) return;
    onLogout();
    throw new Error("로그인 세션이 만료되었습니다. 다시 로그인해 주세요.");
  }

  // 지도는 설비 배치의 기준점을 중앙에 고정한다. 설비는 항상 같은 자리에 그대로 있고,
  // 현재 위치(핀)가 그 기준으로 다가오거나 멀어지는 것으로 보인다.
  const gpsMapCenter = gpsOrigin ? { lat: gpsOrigin.latitude, lon: gpsOrigin.longitude } : null;
  const gpsLiveOffset =
    gpsMapCenter && gpsLivePosition
      ? metersOffsetFromCenter(gpsMapCenter, gpsLivePosition.latitude, gpsLivePosition.longitude)
      : null;
  const gpsLiveDistanceM = gpsLiveOffset ? Math.round(Math.hypot(gpsLiveOffset.x, gpsLiveOffset.y)) : 0;
  // 지도 박스가 실제로 표시하는 반경(대략 GPS_MAP_SIZE_PX/2 ÷ GPS_MAP_SCALE_PX_PER_M, m
  // 단위)보다 멀면 마커가 박스 밖으로 밀려서 overflow:hidden에 잘려 안 보이게 된다.
  const gpsLiveIsOffMap = gpsLiveOffset !== null && isOffsetOffMap(gpsLiveOffset);

  function openDocumentViewer(source: ChatSource) {
    setDocumentViewerTarget({
      documentId: source.document_id,
      documentVersionId: source.document_version_id,
      page: source.page_start ?? source.page ?? null,
      title: source.original_filename || source.title,
    });
  }

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

  // 채팅 답변에 딸려 온 체크리스트(로컬 미리보기)를 위험성평가로 DB에 저장한다.
  // 화면에 보이는 항목 문구를 그대로 보내서, 별도 규칙 엔진이 다시 계산한 다른
  // 체크리스트가 저장되지 않도록 한다(백엔드 `/assessments/from-chat-checklist` 참고).
  // 채팅 체크리스트 저장/재저장. 처음 호출이면(savedAssessmentId 없음) 위험성평가를
  // 새로 만들고, 이미 저장돼 있으면 그 assessment에 이어서 사용한다. 어느 쪽이든
  // checkedIndices(배열 위치 기준)와 현재 항목의 완료 상태가 다른 것만 PATCH해서
  // 반영하므로, 몇 번이고 다시 체크하고 다시 저장할 수 있다.
  //
  // 인덱스(배열 위치)로 대조하는 이유: 백엔드가 최초 저장 시 sequence를 1부터 다시
  // 매기는데, 모델이 만든 원래 항목 목록에 걸러진 항목이 있으면 원래 sequence에
  // 구멍이 생겨 저장 후 sequence와 어긋날 수 있다. 배열 위치는 항상 순서대로
  // 전송·저장되므로 이런 어긋남이 생기지 않는다.
  async function saveChatChecklist(messageIndex: number, message: ChatMessage, checkedIndices: number[]) {
    const items = message.checklistItems ?? [];
    if (!items.length || savingChecklistIndex !== null) return;
    setSavingChecklistIndex(messageIndex);
    setError("");
    try {
      const token = getAccessToken();
      if (!token) throw new Error("체크리스트를 저장하려면 다시 로그인해 주세요.");

      let assessmentId = message.savedAssessmentId ?? null;
      let baseItems = items;
      if (!assessmentId) {
        const response = await fetch(`${getApiBaseUrl()}/api/v1/assessments/from-chat-checklist`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            description: message.sourceQuestion || message.text || "AI 상담 체크리스트",
            checklist_items: items.map((item) => item.content),
          }),
        });
        if (!response.ok) {
          throw new Error(await apiErrorMessage(response, "체크리스트를 저장하지 못했습니다."));
        }
        const payload = await response.json() as AssessmentResponse;
        assessmentId = payload.assessment_id;
        baseItems = payload.checklist_items.map((item): ChatChecklistItem => ({
          id: item.id,
          content: item.content,
          sequence: item.sequence,
          is_required: false,
          is_completed: item.is_completed,
          completed_by_user_id: item.completed_by_user_id,
          completed_at: item.completed_at,
          evidence_chunk_ids: [],
        }));
        // 생성 직후 체크 상태 PATCH 중 일부가 실패하더라도 재시도 시 같은 평가를
        // 이어서 사용하도록, 생성된 ID와 서버 항목을 먼저 화면 상태에 반영한다.
        setMessages((current) => current.map((existing, index) => (
          index === messageIndex
            ? { ...existing, savedAssessmentId: assessmentId, checklistItems: baseItems }
            : existing
        )));
      }

      const checkedIndexSet = new Set(checkedIndices);
      const finalItems: ChatChecklistItem[] = [];
      for (let index = 0; index < baseItems.length; index += 1) {
        const current = baseItems[index];
        const shouldBeCompleted = checkedIndexSet.has(index);
        if (current.id && shouldBeCompleted !== current.is_completed) {
          const patchResponse = await fetch(
            `${getApiBaseUrl()}/api/v1/assessments/${assessmentId}/checklist-items/${current.id}`,
            {
              method: "PATCH",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
              },
              body: JSON.stringify({ is_completed: shouldBeCompleted }),
            },
          );
          if (!patchResponse.ok) {
            throw new Error(await apiErrorMessage(
              patchResponse,
              "체크리스트 완료 상태를 저장하지 못했습니다.",
            ));
          }
          const updated = await patchResponse.json() as ChecklistItemUpdateResponse;
          finalItems.push({
            id: updated.id,
            content: updated.content,
            sequence: updated.sequence,
            is_required: false,
            is_completed: updated.is_completed,
            completed_by_user_id: updated.completed_by_user_id,
            completed_at: updated.completed_at,
            evidence_chunk_ids: [],
          });
          continue;
        }
        finalItems.push(current);
      }

      setMessages((current) => current.map((existing, index) => (
        index === messageIndex
          ? { ...existing, savedAssessmentId: assessmentId, checklistItems: finalItems }
          : existing
      )));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "체크리스트 저장 중 오류가 발생했습니다.");
    } finally {
      setSavingChecklistIndex(null);
    }
  }

  // GPS 근접 안내 체크리스트 저장/재저장. 채팅 체크리스트와 같은 저장 API를 쓰되,
  // task_type="정기 순찰 점검" + 실제 site_name/equipment_name을 함께 보내서
  // 목록 화면에서 채팅 상담 체크리스트와 구분되어 따로 모아 보이게 한다.
  async function saveGpsChecklist(item: NearbyEquipmentItem) {
    if (gpsSource !== "real" || savingGpsEquipmentCode !== null) return;
    const checkedIndexSet = gpsChecklistChecked[item.equipment_code] ?? new Set<number>();
    setSavingGpsEquipmentCode(item.equipment_code);
    setError("");
    try {
      const token = getAccessToken();
      if (!token) throw new Error("체크리스트를 저장하려면 다시 로그인해 주세요.");

      let saved = gpsSavedChecklists[item.equipment_code];
      if (!saved) {
        const response = await fetch(`${getApiBaseUrl()}/api/v1/assessments/from-chat-checklist`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            site_name: item.site_name,
            equipment_name: item.equipment_name,
            task_type: "정기 순찰 점검",
            description: `${item.site_name} ${item.equipment_name} 근접 안전 점검`,
            checklist_items: item.checklist,
          }),
        });
        if (!response.ok) {
          throw new Error(await apiErrorMessage(response, "체크리스트를 저장하지 못했습니다."));
        }
        const payload = await response.json() as AssessmentResponse;
        saved = {
          assessmentId: payload.assessment_id,
          items: payload.checklist_items.map((entry) => ({ id: entry.id, is_completed: entry.is_completed })),
        };
        // 생성 이후 완료 상태 저장에 실패해도 다음 시도에서 새 평가를 중복 생성하지
        // 않도록 서버가 발급한 평가 ID를 즉시 보관한다.
        setGpsSavedChecklists((current) => ({
          ...current,
          [item.equipment_code]: saved!,
        }));
      }

      const assessmentId = saved.assessmentId;
      const nextItems: { id: string | null; is_completed: boolean }[] = [];
      for (let index = 0; index < saved.items.length; index += 1) {
        const current = saved.items[index];
        const shouldBeCompleted = checkedIndexSet.has(index);
        if (current.id && shouldBeCompleted !== current.is_completed) {
          const patchResponse = await fetch(
            `${getApiBaseUrl()}/api/v1/assessments/${assessmentId}/checklist-items/${current.id}`,
            {
              method: "PATCH",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
              },
              body: JSON.stringify({ is_completed: shouldBeCompleted }),
            },
          );
          if (!patchResponse.ok) {
            throw new Error(await apiErrorMessage(
              patchResponse,
              "GPS 체크리스트 완료 상태를 저장하지 못했습니다.",
            ));
          }
          const updated = await patchResponse.json() as ChecklistItemUpdateResponse;
          nextItems.push({ id: updated.id, is_completed: updated.is_completed });
          continue;
        }
        nextItems.push(current);
      }

      setGpsSavedChecklists((current) => ({
        ...current,
        [item.equipment_code]: { assessmentId, items: nextItems },
      }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "체크리스트 저장 중 오류가 발생했습니다.");
    } finally {
      setSavingGpsEquipmentCode(null);
    }
  }

  async function sendChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedQuestion = question.trim();
    if (!submittedQuestion || isChatLoading || isVisionLoading) return;
    const isPhotoQuestion = refersToAttachedPhoto(submittedQuestion);
    const candidatesForAnswer = isPhotoQuestion ? catalogCandidates : [];

    if (
      isPhotoQuestion
      && sitePhotoName
      && visualCategories.length === 0
      && catalogCandidates.length === 0
    ) {
      setQuestion("");
      setMessages((current) => [
        ...current,
        { role: "user", text: submittedQuestion },
        {
          role: "ai",
          text: "현재 사진에서 신뢰할 수 있는 제품 종류를 확인하지 못했습니다. 관련 없는 매뉴얼 검색 결과로 대체하지 않습니다. 대상을 더 가까이 촬영하거나 정면 사진을 다시 첨부해 주세요.",
          warning: "사진 분류 결과 없음",
        },
      ]);
      return;
    }

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
      if (selectedDocumentIds.length > 0 && !token) {
        setMessages((current) => [
          ...current,
          {
            role: "ai",
            text: "선택한 PDF 문서로 답변하려면 다시 로그인해 주세요.",
            warning: "로그인 필요",
          },
        ]);
        setIsChatLoading(false);
        return;
      }
      const response = await fetch(`${getApiBaseUrl()}/api/v1/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          question: submittedQuestion,
          context: {
            site_name: null,
            equipment_name: null,
            manufacturer: null,
            model_number: null,
            component_name: null,
            task_type: null,
            energy_source: null,
            task_description: null,
            visual_summary: visionSummary || null,
            visual_categories: visualCategories,
            visual_features: visualFeatures,
            selected_document_ids: selectedDocumentIds,
          },
        }),
      });
      requireActiveSession(response);
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
        void playSpeech(buildSpeechText({ text: payload.answer, structuredAnswer: payload.structured_answer }));
      }
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "백엔드에 연결할 수 없습니다.";
      setMessages((current) => [...current, { role: "ai", text: message, warning: "검색 결과를 생성하지 못했습니다." }]);
      setError(message);
    } finally {
      setIsChatLoading(false);
    }
  }

  async function addManuals(files: FileList | null) {
    if (!files) return;
    const token = getAccessToken();
    if (!token) {
      onLogout();
      return;
    }
    setManualStatus("문서를 등록하고 로컬 이미지 인덱스를 생성하는 중...");
    let uploadedCount = 0;
    const uploadFailures: string[] = [];
    const visionPending: string[] = [];

    for (const file of Array.from(files)) {
      const clientKey = `upload-${crypto.randomUUID()}`;
      setProcessingProgress((current) => ({
        ...current,
        [clientKey]: {
          client_key: clientKey,
          document_id: "",
          document_version_id: "",
          filename: file.name,
          status: "uploading",
          stage: "uploading",
          attempt: 0,
          progress_percent: 0,
          message: "PDF 서버 전송을 준비하고 있습니다.",
          processed_pages: 0,
          total_pages: 0,
          processed_chunks: 0,
          total_chunks: 0,
          embedded_chunks: 0,
          updated_at: new Date().toISOString(),
          is_terminal: false,
          rag_ready: false,
        },
      }));
      try {
        const uploadBody = new FormData();
        uploadBody.append("file", file);
        uploadBody.append("product_type", form.component_name || "미분류 설비");
        uploadBody.append("model_name", form.model_number || form.equipment_name || "미지정 모델");
        uploadBody.append("manufacturer", form.manufacturer || "미지정 제조사");
        uploadBody.append("access_level", "restricted");
        const uploadPayload = await new Promise<{
          document_id: string;
          document_version_id: string;
          detail?: string;
        }>((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          uploadXhrsRef.current.set(clientKey, xhr);
          xhr.open("POST", `${getApiBaseUrl()}/api/v1/documents/upload`);
          xhr.setRequestHeader("Authorization", `Bearer ${token}`);
          xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable || event.total <= 0) return;
            const percent = Math.min(10, Math.round((event.loaded / event.total) * 10));
            setProcessingProgress((current) => {
              const previous = current[clientKey];
              if (!previous) return current;
              return {
                ...current,
                [clientKey]: {
                  ...previous,
                  progress_percent: Math.max(previous.progress_percent, percent),
                  message: event.loaded >= event.total
                    ? "파일 전송 완료 · 서버에서 업로드를 검증하고 저장하고 있습니다."
                    : `PDF 서버 전송 중 · ${Math.round((event.loaded / event.total) * 100)}%`,
                  updated_at: new Date().toISOString(),
                },
              };
            });
          };
          xhr.onload = () => {
            uploadXhrsRef.current.delete(clientKey);
            const payload = (() => {
              try {
                return JSON.parse(xhr.responseText) as {
                  document_id?: string;
                  document_version_id?: string;
                  detail?: string;
                };
              } catch {
                return null;
              }
            })();
            if (xhr.status === 401) {
              onLogout();
              reject(new Error("로그인 세션이 만료되었습니다. 다시 로그인해 주세요."));
              return;
            }
            if (
              xhr.status < 200
              || xhr.status >= 300
              || !payload?.document_id
              || !payload.document_version_id
            ) {
              reject(new Error(payload?.detail || `${file.name} 문서 등록 실패`));
              return;
            }
            resolve({
              document_id: payload.document_id,
              document_version_id: payload.document_version_id,
            });
          };
          xhr.onerror = () => {
            uploadXhrsRef.current.delete(clientKey);
            reject(new Error(`${file.name} 업로드 중 네트워크 오류가 발생했습니다.`));
          };
          xhr.onabort = () => {
            uploadXhrsRef.current.delete(clientKey);
            reject(new Error(`${file.name} 업로드가 취소되었습니다.`));
          };
          xhr.send(uploadBody);
        });

        uploadedCount += 1;
        setManuals((current) => Array.from(new Set([...current, file.name])));
        setSelectedDocumentIds((current) => Array.from(new Set([...current, uploadPayload.document_id])));
        setProcessingProgress((current) => {
          const next = { ...current };
          delete next[clientKey];
          next[uploadPayload.document_id] = {
            client_key: uploadPayload.document_id,
            document_id: uploadPayload.document_id,
            document_version_id: uploadPayload.document_version_id,
            filename: file.name,
            status: "pending",
            stage: "queued",
            attempt: 0,
            progress_percent: 15,
            message: "PDF 처리 대기 중",
            processed_pages: 0,
            total_pages: 0,
            processed_chunks: 0,
            total_chunks: 0,
            embedded_chunks: 0,
            updated_at: new Date().toISOString(),
            is_terminal: false,
            rag_ready: false,
          };
          return next;
        });
        startProgressPolling(uploadPayload.document_id, file.name);

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
        const failureMessage = requestError instanceof Error
          ? requestError.message
          : `${file.name} 문서 등록 실패`;
        uploadFailures.push(failureMessage);
        setProcessingProgress((current) => {
          const previous = current[clientKey];
          if (!previous) return current;
          return {
            ...current,
            [clientKey]: {
              ...previous,
              status: "failed",
              stage: "failed",
              message: failureMessage,
              is_terminal: true,
              updated_at: new Date().toISOString(),
            },
          };
        });
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

  async function reindexManual(documentId: string, filename: string) {
    const token = getAccessToken();
    if (!token) {
      onLogout();
      return;
    }
    setManualStatus(`${filename} 비전 인덱스를 다시 생성하는 중...`);
    const body = new FormData();
    body.append("document_id", documentId);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/vision/catalog/index`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body,
      });
      requireActiveSession(response);
      const payload = await response.json().catch(() => null) as { document_id?: string; detail?: string } | null;
      if (!response.ok || payload?.document_id !== documentId) {
        throw new Error(payload?.detail || `${filename} 비전 인덱스 재생성 실패`);
      }
      setCatalogCandidates([]);
      setVisionSummary("");
      setManualStatus(`${filename} 비전 인덱스를 최신 형식으로 다시 생성했습니다.`);
    } catch (requestError) {
      setManualStatus(requestError instanceof Error ? requestError.message : `${filename} 비전 인덱스 재생성 실패`);
    }
  }

  async function deleteManual(documentId: string, filename: string) {
    const token = getAccessToken();
    if (!token) {
      onLogout();
      return;
    }
    setManualStatus(`${filename} 삭제하는 중...`);
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/documents/${documentId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      requireActiveSession(response);
      if (!response.ok) {
        throw new Error(await apiErrorMessage(response, `${filename} 삭제에 실패했습니다.`));
      }
      setUserDocuments((current) => current.filter((document) => document.document_id !== documentId));
      setSelectedDocumentIds((current) => current.filter((id) => id !== documentId));
      setCatalogCandidates([]);
      setVisionSummary("");
      setManualStatus(`${filename}을(를) 삭제했습니다.`);
    } catch (requestError) {
      setManualStatus(requestError instanceof Error ? requestError.message : `${filename} 삭제에 실패했습니다.`);
    }
  }

  async function analyzePhoto(
    file: File | undefined,
    documentIds: string[] = selectedDocumentIds,
  ) {
    if (!file) return;
    sitePhotoFileRef.current = file;
    const analysisStartedAt = performance.now();
    setIsVisionLoading(true);
    const token = getAccessToken();
    if (!token) {
      setIsVisionLoading(false);
      onLogout();
      return;
    }
    setSitePhotoName(file.name);
    setVisionStatus("로컬 이미지 분석 중...");
    setVisionElapsedMs(null);
    setVisionSummary("");
    setCatalogCandidates([]);
    setVisualCategories([]);
    setVisualFeatures([]);
    const requestId = ++visionRequestIdRef.current;
    type VisionPayload = {
      items?: Array<{
        equipment_type?: string | null;
        component_name?: string | null;
        description?: string | null;
        visible_conditions?: string[];
      }>;
      raw_visual_description?: string;
      extracted_markdown?: string;
      catalog_candidates?: CatalogCandidate[];
      warnings?: string[];
      detail?: string;
    };
    const requestAnalysis = async () => {
      const body = new FormData();
      body.append("file", file);
      body.append("document_ids", JSON.stringify(documentIds));
      body.append("analysis_mode", "deep");
      const response = await fetch(`${getApiBaseUrl()}/api/v1/vision/catalog/match`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body,
      });
      requireActiveSession(response);
      const payload = await response.json().catch(() => null) as VisionPayload | null;
      if (!response.ok) throw new Error(payload?.detail || "이미지 분석 실패");
      return payload;
    };
    const applyPayload = (payload: VisionPayload | null) => {
      if (visionRequestIdRef.current !== requestId) return;
      const candidates = payload?.catalog_candidates ?? [];
      const items = payload?.items ?? [];
      const categories = Array.from(new Set([
        ...candidates.map((item) => item.visual_category),
        ...items.flatMap((item) => [item.component_name, item.equipment_type]),
      ].filter((value): value is string => Boolean(value?.trim()))));
      const features = Array.from(new Set([
        ...candidates.flatMap((item) => item.visual_features || []),
        ...items.flatMap((item) => [item.description, ...(item.visible_conditions || [])]),
      ].filter((value): value is string => Boolean(value?.trim()))));
      setVisualCategories(categories);
      setVisualFeatures(features);
      setCatalogCandidates(candidates);
      setVisionSummary([
        items.length ? `로컬 VLM 관찰 결과:\n${items.map((item, index) => `${index + 1}. ${item.component_name || item.equipment_type || "종류 확인 불가"}${item.description ? ` · ${item.description}` : ""}`).join("\n")}` : "",
        candidates.length ? `카탈로그 외형 유사 후보(동일 제품 확정 아님):\n${candidates.map((item, index) => `${index + 1}. ${item.visual_category || "종류 확인 불가"}, ${item.filename} ${item.page}페이지, 유사도 ${(item.similarity * 100).toFixed(1)}%, 특징 ${item.visual_features?.join(", ") || "확인 불가"}`).join("\n")}` : "신뢰 임계값을 넘는 카탈로그 후보 없음",
        payload?.extracted_markdown ? `로컬 OCR 확인 내용:\n${payload.extracted_markdown}` : "",
        payload?.raw_visual_description ? `로컬 비전 참고 설명(각인·규격 확정 근거 아님):\n${payload.raw_visual_description}` : "",
      ].filter(Boolean).join("\n\n"));
      setVisionStatus(payload?.warnings?.length ? `정밀 분석 완료 · ${payload.warnings.join(" · ")}` : "정밀 분석 완료");
    };
    try {
      const payload = await requestAnalysis();
      applyPayload(payload);
      if (visionRequestIdRef.current === requestId) {
        setVisionElapsedMs(performance.now() - analysisStartedAt);
      }
    } catch (requestError) {
      if (visionRequestIdRef.current === requestId) {
        setVisionStatus(requestError instanceof Error ? requestError.message : "로컬 비전 서비스 연결 실패");
      }
    } finally {
      if (visionRequestIdRef.current === requestId) setIsVisionLoading(false);
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

  // 기준점(설비 배치)을 point로 재설정하는 유일한 경로. "이 위치로 다시 보정" 버튼이
  // 이 함수를 거쳐, 설비 배치 자체를 그 좌표로 명시적으로 옮긴다. 실제 GPS 위치는
  // (최초 fix를 포함해) 이 기준점을 자동으로 건드리지 않는다 — 그래야 실제 위치가
  // 설비 반경 안으로 "이동해 들어와야" 체크리스트가 활성화되는 것이 유지된다.
  function recalibrateTo(point: { latitude: number; longitude: number }, options: { source: "default" | "real" }) {
    gpsOriginRef.current = point;
    // 기준점을 다시 잡는 것이므로, 이 시점부터는 실제 위치 변화가 이 기준점
    // 대비로 다시 실시간 반영되도록 수동 override를 해제한다.
    gpsManualOverrideRef.current = false;
    setGpsOrigin(point);
    setGpsLivePosition(point);
    setGpsSource(options.source);
    // 입력창을 이 기준점으로 동기화해 둔다. 안 그러면 기준점이 옮겨간 뒤에도
    // 입력창엔 옛날 값이 그대로 남아서, "조금만 옮겨서 테스트"해도 실제로는
    // 기준점에서 수백~수천m 떨어진 값을 건드리는 셈이 되어 매번 반경 밖으로 나온다.
    setManualLatitude(point.latitude.toFixed(6));
    setManualLongitude(point.longitude.toFixed(6));
    void loadCalibratedEquipment(point);
    void checkLocation(point.latitude, point.longitude, point);
  }

  function recalibrateManualLocation() {
    const point = parseManualCoordinates();
    if (!point) return;
    recalibrateTo(point, { source: "default" });
  }

  // 주어진 좌표를 "이 위치로 확인"한 것과 동일하게 처리한다. 입력창 값을 직접 파싱하는
  // checkManualLocation과, 지도를 클릭해 좌표를 바로 넘기는 handleMapClick이 공유한다.
  function checkPointAsManualLocation(point: { latitude: number; longitude: number }) {
    // 수동으로 확인한 결과이므로, 직전에 실제 위치로 표시돼 있었더라도 지금 보여주는
    // 결과의 출처는 "기본 테스트 좌표"로 명확히 되돌린다. 이어서 들어오는 실제 위치
    // 업데이트가 이 결과를 곧바로 덮어쓰지 않도록 잠근다.
    // (기준점은 마운트 시/실제 위치 최초 확인 시 이미 설정돼 있으므로 건드리지 않는다.)
    gpsManualOverrideRef.current = true;
    setGpsSource("default");
    setManualLatitude(point.latitude.toFixed(6));
    setManualLongitude(point.longitude.toFixed(6));
    setGpsLivePosition(point);
    void checkLocation(point.latitude, point.longitude, gpsOriginRef.current ?? point);
  }

  function checkManualLocation() {
    const point = parseManualCoordinates();
    if (!point) return;
    checkPointAsManualLocation(point);
  }

  // 지도를 클릭한 픽셀 좌표를, 화면에 그린 것과 같은 축척(GPS_MAP_SCALE_PX_PER_M)으로
  // 기준점(gpsMapCenter) 기준 위경도로 역산해 그 위치를 "이 위치로 확인"한 것처럼 반영한다.
  // metersOffsetFromCenter의 역변환이다.
  function handleMapClick(event: React.MouseEvent<HTMLDivElement>) {
    if (!gpsMapCenter) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const offsetXm = (event.clientX - rect.left - GPS_MAP_SIZE_PX / 2) / GPS_MAP_SCALE_PX_PER_M;
    const offsetYm = (event.clientY - rect.top - GPS_MAP_SIZE_PX / 2) / GPS_MAP_SCALE_PX_PER_M;
    checkPointAsManualLocation({
      latitude: gpsMapCenter.lat - offsetYm / METERS_PER_DEG_LAT,
      longitude: gpsMapCenter.lon + offsetXm / metersPerDegLon(gpsMapCenter.lat),
    });
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

    const latestAiMessage = [...messages].reverse().find((message) => message.role === "ai");
    const text = (latestAiMessage ? buildSpeechText(latestAiMessage) : null) ?? (result
      ? `현재 분석된 위험요인은 ${result.hazards.length}건입니다. ${result.hazards.map((hazard) => `${hazard.name}. ${hazard.safety_actions.join(". ")}`).join(". ")}`
      : "매뉴얼을 선택하고 AI 상담에서 질문한 뒤 음성 안전 안내를 들을 수 있습니다.");

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

  const selectMobileWorkspaceTab = useCallback((tab: MobileWorkspaceTab) => {
    setMobileWorkspaceTab(tab);
    if (tab === "evidence") setIsDocumentPanelOpen(true);
    if (tab === "safety") setIsStatusPanelOpen(true);
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }, []);

  function handleMobileSwipeStart(event: ReactPointerEvent<HTMLDivElement>) {
    if (!window.matchMedia("(max-width: 767px)").matches) return;
    const target = event.target;
    if (event.button !== 0 || !(target instanceof HTMLElement)) return;
    const hasInteractiveTarget = Boolean(target.closest(
      "a, button, input, label, select, summary, textarea, [contenteditable='true'], [role='tab']",
    ));
    mobileSwipeRef.current = {
      active: !hasInteractiveTarget,
      startX: event.clientX,
      startY: event.clientY,
      lastX: event.clientX,
      lastY: event.clientY,
    };
  }

  function handleMobileSwipeMove(event: ReactPointerEvent<HTMLDivElement>) {
    const swipe = mobileSwipeRef.current;
    if (!swipe?.active) return;
    swipe.lastX = event.clientX;
    swipe.lastY = event.clientY;
  }

  function handleMobileSwipeEnd() {
    const swipe = mobileSwipeRef.current;
    mobileSwipeRef.current = null;
    if (!swipe?.active) return;

    const horizontalDistance = swipe.lastX - swipe.startX;
    const verticalDistance = swipe.lastY - swipe.startY;
    if (
      Math.abs(horizontalDistance) < MOBILE_SWIPE_MIN_DISTANCE_PX
      || Math.abs(horizontalDistance) <= Math.abs(verticalDistance) * MOBILE_SWIPE_DIRECTION_RATIO
    ) return;

    const currentIndex = MOBILE_WORKSPACE_TAB_ORDER.indexOf(mobileWorkspaceTab);
    const nextIndex = horizontalDistance < 0 ? currentIndex + 1 : currentIndex - 1;
    const nextTab = MOBILE_WORKSPACE_TAB_ORDER[nextIndex];
    if (nextTab) selectMobileWorkspaceTab(nextTab);
  }

  return (
    <main className={`prototype-shell ${fontClass}`}>
      <WorkspaceHeader
        displayName={displayName}
        locationStatus={locationStatus}
        gpsSource={gpsSource}
        isRecording={isRecording}
        isTranscribing={isTranscribing}
        isSpeaking={isSpeaking}
        volume={volume}
        fontSize={fontSize}
        autoSpeak={autoSpeak}
        onVoiceInput={toggleVoiceInput}
        onSpeakGuidance={speakGuidance}
        onHistory={onHistory}
        onAssessments={openAssessmentsDrawer}
        onVolumeChange={setVolume}
        onFontSizeChange={setFontSize}
        onAutoSpeakChange={setAutoSpeak}
        onClearConversation={() => setMessages([])}
        onClearManuals={() => {
          setManuals([]);
          setSelectedDocumentIds([]);
          setCatalogCandidates([]);
          setVisionSummary("");
          setManualStatus("매뉴얼 선택을 초기화했습니다.");
        }}
        onLogout={onLogout}
        notificationCenter={(
          <NotificationCenter
            apiBaseUrl={getApiBaseUrl()}
            token={getAccessToken()}
            onUnauthorized={onLogout}
            onDocumentApproved={async () => refreshMyDocuments()}
          />
        )}
      />

      <div
        className="workspace-content"
        data-mobile-tab={mobileWorkspaceTab}
        onPointerDown={handleMobileSwipeStart}
        onPointerMove={handleMobileSwipeMove}
        onPointerUp={handleMobileSwipeEnd}
        onPointerCancel={() => {
          mobileSwipeRef.current = null;
        }}
      >
        <details
          className="workflow-hero mobile-tab-panel mobile-tab-more"
          id="mobile-workspace-panel-more"
          role="tabpanel"
          aria-labelledby="mobile-workspace-tab-more"
        >
          <summary>
            <span><span className="eyebrow">SafeMaint 현장 안전 작업 콘솔</span><strong id="workflow-title">문서 선택 → AI 상담 → 현장 확인</strong></span>
            <span className="workflow-toggle-label">사용 안내</span>
          </summary>
          <div className="workflow-hero-content" aria-labelledby="workflow-title">
            <p>매뉴얼과 현장 사진을 바탕으로 부품 정보·작업 절차·TBM 항목을 질문하고, 표시된 근거를 직접 확인하세요.</p>
            <ol className="workflow-steps" aria-label="작업 진행 순서">
              <li className={manuals.length ? "complete" : "active"}><span>1</span><strong>문서·사진</strong></li>
              <li className={messages.length ? "complete" : manuals.length ? "active" : ""}><span>2</span><strong>AI 질문</strong></li>
              <li className={result ? "complete" : messages.length ? "active" : ""}><span>3</span><strong>위험·TBM</strong></li>
            </ol>
          </div>
        </details>

      {error && <p className="workspace-error error-message" role="alert">{error}</p>}

      <details className="assessment-drawer gps-drawer mobile-tab-panel mobile-tab-more">
        <summary>
          <span className="gps-summary-title"><InterfaceIcon name="location" /><strong>가상 GPS 위치 도구</strong></span>
          <span className={isGpsChecking ? "status-pill active" : "status-pill"}>{locationStatus}</span>
          <span className="panel-tag muted">{gpsSource === "real" ? "실제 위치 사용 중" : "기본 테스트 좌표 사용 중"}</span>
        </summary>
        <section className="panel gps-map-panel" aria-label="가상 GPS 자동 위치 추적">
        <p className="muted-copy">
          브라우저가 실제 위치를 확인하면 자동으로 그 위치를 보여주고, 실패하면 기본 테스트 좌표를 사용합니다.
          아래 위경도 값을 바꾸거나 <strong>지도를 클릭</strong>하면 설비 배치 기준점은 그대로 둔 채 그 위치에서의 결과만
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
          <div
            className="gps-map"
            style={{ width: GPS_MAP_SIZE_PX, height: GPS_MAP_SIZE_PX }}
            onClick={handleMapClick}
            title="지도를 클릭하면 그 위치로 테스트 좌표가 이동합니다"
          >
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
                    {gpsSource !== "real" && (
                      <p className="muted-copy">
                        테스트 좌표로 확인한 결과입니다. 실제 위치가 설비 근처로 들어오면 체크할 수 있습니다.
                      </p>
                    )}
                    {item.checklist.map((entry, entryIndex) => {
                      const checkedSet = gpsChecklistChecked[item.equipment_code];
                      const isChecked = checkedSet ? checkedSet.has(entryIndex) : false;
                      return (
                        <label key={entry}>
                          <input
                            type="checkbox"
                            checked={isChecked}
                            disabled={gpsSource !== "real"}
                            onChange={(event) => setGpsChecklistChecked((current) => {
                              const next = new Set(current[item.equipment_code] ?? []);
                              if (event.target.checked) next.add(entryIndex);
                              else next.delete(entryIndex);
                              return { ...current, [item.equipment_code]: next };
                            })}
                          />
                          <span>{entry}</span>
                        </label>
                      );
                    })}
                    {gpsSource === "real" && (
                      <button
                        type="button"
                        className="chat-checklist-save"
                        onClick={() => void saveGpsChecklist(item)}
                        disabled={savingGpsEquipmentCode === item.equipment_code}
                      >
                        {savingGpsEquipmentCode === item.equipment_code
                          ? "저장 중..."
                          : gpsSavedChecklists[item.equipment_code] ? "변경사항 저장" : "이 체크리스트 저장"}
                      </button>
                    )}
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

      <details
        className="mobile-section-drawer console-status-drawer mobile-tab-panel mobile-tab-safety"
        id="mobile-workspace-panel-safety"
        role="tabpanel"
        aria-labelledby="mobile-workspace-tab-safety"
        open={isStatusPanelOpen}
        onToggle={(event) => setIsStatusPanelOpen(event.currentTarget.open)}
      >
        <summary>
          <span className="mobile-section-heading"><strong>현재 안전 상태</strong></span>
          <span className="mobile-section-summary-meta">
            <small>{highestRisk === "high" ? "작업 중지" : highestRisk === "medium" ? "관리자 확인" : highestRisk === "low" ? "기본조치" : "상담 대기"}</small>
            <span className="mobile-section-toggle">
              {isStatusPanelOpen ? "접기" : "펼치기"}
              <span aria-hidden="true">{isStatusPanelOpen ? "▴" : "▾"}</span>
            </span>
          </span>
        </summary>
        <div className="mobile-section-drawer-body">
          <section className={`console-status-strip risk-${highestRisk}`} aria-label="현재 안전 작업 상태">
            <div className="console-status-primary">
              <span className="risk-symbol" aria-hidden="true"><span /></span>
              <span><small>현재 판단</small><strong>{highestRisk === "high" ? "작업 중지 필요" : highestRisk === "medium" ? "관리자 확인 필요" : highestRisk === "low" ? "기본조치 확인" : "상담 대기"}</strong></span>
            </div>
            <dl className="console-status-metrics">
              <div><dt>예상 위험도</dt><dd>{!result ? "확인 불가" : highestRisk === "high" ? levelLabel.high : highestRisk === "medium" ? levelLabel.medium : levelLabel.low}</dd></div>
              <div><dt>근거 상태</dt><dd>{result?.evidence_status === "connected" ? `${result.evidence.length}건 연결` : "연결되지 않음"}</dd></div>
              <div><dt>선택 문서</dt><dd>{selectedDocumentIds.length ? `${selectedDocumentIds.length}개` : "선택되지 않음"}</dd></div>
              <div><dt>현장 위치</dt><dd>{locationStatus || "확인 불가"}</dd></div>
            </dl>
            <div className="console-status-warning">
              <strong>{result ? `위험요인 ${result.hazards.length}건 확인` : "분석 전"}</strong>
              <span>{result?.disclaimer || "AI는 작업을 승인하지 않습니다. 표시된 근거와 현장 상태를 직접 확인하세요."}</span>
            </div>
          </section>
        </div>
      </details>

      <div className="field-console-grid">
        <details
          className="console-documents-panel mobile-tab-panel mobile-tab-evidence"
          id="mobile-workspace-panel-evidence"
          role="tabpanel"
          aria-labelledby="mobile-workspace-tab-evidence"
          open={isDocumentPanelOpen}
          onToggle={(event) => setIsDocumentPanelOpen(event.currentTarget.open)}
        >
          <summary>
            <span className="console-documents-heading">
              <InterfaceIcon name="document" />
              <strong>근거 자료</strong>
            </span>
            <span className="console-documents-summary-meta">
              <small>{selectedDocumentIds.length}개 선택</small>
              <span className="console-documents-toggle">
                {isDocumentPanelOpen ? "접기" : "펼치기"}
                <span aria-hidden="true">{isDocumentPanelOpen ? "▴" : "▾"}</span>
              </span>
            </span>
          </summary>
          <div className="console-document-body">
            <ManualManager
              manuals={manuals}
              documents={userDocuments}
              processingProgress={Object.values(processingProgress)}
              selectedDocumentIds={selectedDocumentIds}
              manualStatus={manualStatus}
              sitePhotoName={sitePhotoName}
              visionStatus={visionStatus}
              visionElapsedLabel={visionElapsedMs !== null && visionStatus.startsWith("정밀 분석 완료")
                ? formatElapsedTime(visionElapsedMs)
                : null}
              isVisionLoading={isVisionLoading}
              onAddManuals={(files) => void addManuals(files)}
              onAddPhoto={(file) => void analyzePhoto(file)}
              onReindexDocument={(documentId, filename) => void reindexManual(documentId, filename)}
              onDeleteDocument={(documentId, filename) => void deleteManual(documentId, filename)}
              onToggleDocument={(documentId) => {
                const nextDocumentIds = selectedDocumentIds.includes(documentId)
                  ? selectedDocumentIds.filter((id) => id !== documentId)
                  : [...selectedDocumentIds, documentId];
                setSelectedDocumentIds(nextDocumentIds);
                setCatalogCandidates([]);
                setVisionSummary("");
                if (sitePhotoFileRef.current) {
                  void analyzePhoto(sitePhotoFileRef.current, nextDocumentIds);
                }
              }}
              onRemoveLegacyManual={(index) => {
                setManuals((current) => current.filter((_, itemIndex) => itemIndex !== index));
                setSelectedDocumentIds((current) => current.filter((_, itemIndex) => itemIndex !== index));
                setCatalogCandidates([]);
                setVisionSummary("");
              }}
              onRefreshDocuments={() => {
                void refreshMyDocuments()
                  .catch(() => setManualStatus("문서 상태를 새로고치지 못했습니다."));
              }}
            />
          </div>
        </details>

        <section
          className="chat-stage upgraded-chat mobile-tab-panel mobile-tab-chat"
          id="mobile-workspace-panel-chat"
          role="tabpanel"
          aria-labelledby="mobile-workspace-tab-chat chat-title"
        >
          <div className="chat-heading"><div><span className="section-number">02</span><div><strong id="chat-title">SafeMaint AI 상담</strong><span>질문과 선택한 근거 문서를 바탕으로 답변합니다.</span></div></div><button type="button" onClick={() => setMessages([])}>대화 지우기</button></div>
          <div className="chat-history">
            {messages.length === 0 ? (
              <div className="chat-empty"><span className="chat-empty-icon"><InterfaceIcon name="document" /></span><strong>무엇을 확인할까요?</strong><p>문서 내용, 부품 용도, 설치·점검 방법을 질문해 보세요.</p><small>예: “라이트커튼 설치 시 주의사항을 알려줘”</small></div>
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
                  ? <ChatAnswerContent
                      answer={message.text}
                      structuredAnswer={message.structuredAnswer}
                      sources={message.sources ?? []}
                      warning={message.warning}
                      onOpenDocument={openDocumentViewer}
                    />
                  : <p className="chat-answer-text">{message.text}</p>}
                {message.catalogCandidates && message.catalogCandidates.length > 0 && (
                  <div className="catalog-candidate-list">
                    <strong>사진과 유사한 PDF 페이지 후보</strong>
                    <div className="catalog-candidate-grid">
                      {message.catalogCandidates.map((candidate, candidateIndex) => (
                        <article className="catalog-candidate-card" key={`${candidate.document_id}-${candidate.page}-${candidate.image_index}`}>
                          <SecureCandidateImage candidate={candidate} alt={`후보 ${candidateIndex + 1}`} />
                          <div>
                            <strong>후보 {candidateIndex + 1}{candidate.visual_category ? ` · ${candidate.visual_category}` : ""}</strong>
                            <span>{candidate.filename} · {candidate.page}페이지</span>
                            <span>유사도 {(candidate.similarity * 100).toFixed(1)}% · 신뢰 {candidate.confidence}</span>
                            {candidate.visual_features && candidate.visual_features.length > 0 && <p>{candidate.visual_features.join(" · ")}</p>}
                            {candidate.page_excerpt && <small>{candidate.page_excerpt}</small>}
                          </div>
                        </article>
                      ))}
                    </div>
                    <p className="catalog-candidate-caution">벡터 유사도 후보이며 제품명·동일 모델·규격을 확정한 결과가 아닙니다. PDF 원문을 직접 확인해 주세요.</p>
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

        <details
          className="mobile-section-drawer console-safety-drawer mobile-tab-panel mobile-tab-safety"
          open={isSafetyPanelOpen}
          onToggle={(event) => setIsSafetyPanelOpen(event.currentTarget.open)}
        >
          <summary>
            <span className="mobile-section-heading"><strong>현장 안전 확인</strong></span>
            <span className="mobile-section-summary-meta">
              <small>보호구·TBM</small>
              <span className="mobile-section-toggle">
                {isSafetyPanelOpen ? "접기" : "펼치기"}
                <span aria-hidden="true">{isSafetyPanelOpen ? "▴" : "▾"}</span>
              </span>
            </span>
          </summary>
          <div className="mobile-section-drawer-body">
            <aside className="console-safety-column" aria-label="현장 확인">
              <article className="panel field-status-panel">
                <div className="panel-heading compact-heading"><h2>현장 확인</h2><span className="panel-tag">실제 상태</span></div>
                <dl>
                  <div><dt>문서</dt><dd>{selectedDocumentIds.length ? `${selectedDocumentIds.length}개 선택` : "선택되지 않음"}</dd></div>
                  <div><dt>근거</dt><dd>{result?.evidence_status === "connected" ? "연결됨" : "연결되지 않음"}</dd></div>
                  <div><dt>분석</dt><dd>{result ? "결과 확인 필요" : "확인 불가"}</dd></div>
                  <div><dt>위치</dt><dd>{locationStatus || "확인 불가"}</dd></div>
                </dl>
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
              <article className="panel console-tbm-panel">
                <div className="panel-heading compact-heading">
                  <h2>TBM 현장 체크</h2>
                  <span className="panel-tag muted">{latestChecklistMessage || result ? "분석 결과" : "대기"}</span>
                </div>
                {latestChecklistMessage ? (
                  <ChatChecklist
                    items={latestChecklistMessage.checklistItems ?? []}
                    savedAssessmentId={latestChecklistMessage.savedAssessmentId ?? null}
                    isSaving={savingChecklistIndex === latestChecklistMessageIndex}
                    onSave={(checkedIndices) => void saveChatChecklist(
                      latestChecklistMessageIndex,
                      latestChecklistMessage,
                      checkedIndices,
                    )}
                  />
                ) : result ? (
                  <TbmChecklist
                    result={result}
                    persistence={savedAssessmentId === result.assessment_id ? "saved" : "preview"}
                    isSavingAssessment={isSavingAssessment}
                    pendingItemIds={pendingChecklistItemIds}
                    error={checklistError}
                    onSave={() => void saveAssessment()}
                    onToggle={(item, isCompleted) => void updateChecklistItem(item, isCompleted)}
                  />
                ) : (
                  <div className="console-empty-state"><strong>분석 결과가 없습니다.</strong><span>AI 상담 후 생성된 TBM 항목을 이 영역에서 확인할 수 있습니다.</span></div>
                )}
              </article>
            </aside>
          </div>
        </details>
      </div>

      <details className="admin-tools-drawer mobile-tab-panel mobile-tab-more">
        <summary>
          <span><InterfaceIcon name="document" /><strong>문서 승인 관리</strong></span>
          <small>관리자 권한이 있는 경우에만 내용이 표시됩니다.</small>
        </summary>
        <DocumentReviewPanel
          apiBaseUrl={getApiBaseUrl()}
          token={getAccessToken()}
          onApproved={async () => refreshMyDocuments()}
        />
      </details>

      <details
        className="admin-tools-drawer mobile-tab-panel mobile-tab-more"
        ref={assessmentsDrawerRef}
        onToggle={(event) => {
          // 패널이 계속 마운트돼 있는 채라(채팅 유지 목적) 열 때마다 최신 상태를
          // 다시 불러오도록, 열릴 때만 key를 바꿔 강제로 새로 마운트한다.
          if (event.currentTarget.open) setAssessmentsOpenCount((count) => count + 1);
        }}
      >
        <summary><span><InterfaceIcon name="document" /><strong>저장된 체크리스트</strong></span><small>채팅과 GPS 근접 안내에서 저장한 위험성평가 체크리스트 목록입니다.</small></summary>
        <AssessmentsPanel key={assessmentsOpenCount} />
      </details>

      {result && <details className="assessment-drawer mobile-tab-panel mobile-tab-safety">
        <summary><span><span className="section-number">03</span><strong>상세 위험성평가</strong></span><small>분석된 위험요인과 근거를 상세 검토합니다.</small></summary>
        <section className="search-progress">
          <div className="panel-heading compact-heading"><div><span className="section-number">진행</span><h2>분석 과정</h2></div><span className="status-pill">완료</span></div>
          <div className="progress-steps">{["질문 분석", "사고사례 검색", "법령·KOSHA 검색", "매뉴얼 검색", "재정렬", "결과 생성"].map((step) => <div className="done" key={step}><span>✓</span><strong>{step}</strong></div>)}</div>
        </section>
        <div className="assessment-result-layout">
          <section className="panel result-panel" aria-live="polite">
            <div className="panel-heading"><div><span className="section-number">결과</span><h2>위험성평가 결과</h2></div><span className="panel-tag muted">초안</span></div>
            <div className="result-tabs">
              <button type="button" className={activeTab === "summary" ? "active" : ""} onClick={() => setActiveTab("summary")}>위험요인</button>
              <button type="button" className={activeTab === "accidents" ? "active" : ""} onClick={() => setActiveTab("accidents")}>유사 사고</button>
              <button type="button" className={activeTab === "evidence" ? "active" : ""} onClick={() => setActiveTab("evidence")}>근거 문서</button>
            </div>
            {activeTab === "summary"
              ? <AssessmentResult result={result} mode="hazards" />
              : activeTab === "accidents"
                ? <SimilarAccidentPanel result={result} />
                : <EvidencePanel result={result} manuals={manuals} />}
          </section>
        </div>
      </details>}

      <footer className="safety-footer mobile-tab-panel mobile-tab-safety">본 결과는 작업 전 검토를 위한 초안이며, 현장 안전관리자의 최종 확인과 승인 없이 작업을 시작할 수 없습니다.</footer>

      <nav className="mobile-workspace-tabs" role="tablist" aria-label="모바일 작업 화면">
        <button
          type="button"
          id="mobile-workspace-tab-chat"
          role="tab"
          aria-selected={mobileWorkspaceTab === "chat"}
          aria-controls="mobile-workspace-panel-chat"
          className={mobileWorkspaceTab === "chat" ? "active" : ""}
          onClick={() => selectMobileWorkspaceTab("chat")}
        >
          <InterfaceIcon name="speaker" />
          <span>AI 상담</span>
        </button>
        <button
          type="button"
          id="mobile-workspace-tab-evidence"
          role="tab"
          aria-selected={mobileWorkspaceTab === "evidence"}
          aria-controls="mobile-workspace-panel-evidence"
          className={mobileWorkspaceTab === "evidence" ? "active" : ""}
          onClick={() => selectMobileWorkspaceTab("evidence")}
        >
          <InterfaceIcon name="document" />
          <span>근거 자료</span>
          {selectedDocumentIds.length > 0 && (
            <small className="mobile-tab-badge" aria-label={`선택 문서 ${selectedDocumentIds.length}개`}>
              {selectedDocumentIds.length}
            </small>
          )}
        </button>
        <button
          type="button"
          id="mobile-workspace-tab-safety"
          role="tab"
          aria-selected={mobileWorkspaceTab === "safety"}
          aria-controls="mobile-workspace-panel-safety"
          className={mobileWorkspaceTab === "safety" ? "active" : ""}
          onClick={() => selectMobileWorkspaceTab("safety")}
        >
          <InterfaceIcon name="bell" />
          <span>안전 확인</span>
          {result && result.hazards.length > 0 && (
            <small className="mobile-tab-badge" aria-label={`위험요인 ${result.hazards.length}건`}>
              {result.hazards.length}
            </small>
          )}
        </button>
        <button
          type="button"
          id="mobile-workspace-tab-more"
          role="tab"
          aria-selected={mobileWorkspaceTab === "more"}
          aria-controls="mobile-workspace-panel-more"
          className={mobileWorkspaceTab === "more" ? "active" : ""}
          onClick={() => selectMobileWorkspaceTab("more")}
        >
          <InterfaceIcon name="settings" />
          <span>더보기</span>
        </button>
      </nav>
      </div>
      {documentViewerTarget && (
        <DocumentViewerModal target={documentViewerTarget} onClose={() => setDocumentViewerTarget(null)} />
      )}
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
