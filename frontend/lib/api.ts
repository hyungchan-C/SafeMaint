const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function getApiBaseUrl() {
  if (typeof window === "undefined") return API_BASE_URL;
  const configured = new URL(API_BASE_URL);
  if ((configured.hostname === "localhost" || configured.hostname === "127.0.0.1") && window.location.hostname !== configured.hostname) {
    configured.hostname = window.location.hostname;
  }
  return configured.toString().replace(/\/$/, "");
}
