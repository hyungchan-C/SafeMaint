const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function getApiBaseUrl() {
  if (typeof window === "undefined") return API_BASE_URL;
  const configured = new URL(API_BASE_URL);
  if ((configured.hostname === "localhost" || configured.hostname === "127.0.0.1") && window.location.hostname !== configured.hostname) {
    configured.hostname = window.location.hostname;
  }
  return configured.toString().replace(/\/$/, "");
}

// app/page.tsx의 STORAGE_KEYS.session과 같은 키를 가리킨다. 로그인 세션 자체의
// 읽기/쓰기/삭제는 거기서 계속 처리하고, 여기서는 인증 토큰이 필요한 개별
// 컴포넌트(예: DocumentViewerModal)가 공통으로 쓸 수 있도록 토큰만 뽑아 준다.
const SESSION_STORAGE_KEY = "safemaint.session";

export function getAccessToken(): string {
  if (typeof window === "undefined") return "";
  try {
    const raw = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return "";
    const parsed = JSON.parse(raw) as { accessToken?: string } | null;
    return parsed?.accessToken ?? "";
  } catch {
    return "";
  }
}
