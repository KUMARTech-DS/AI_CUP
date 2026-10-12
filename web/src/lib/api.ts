export type Envelope<T> = {
  data: T;
  meta: {
    request_id: string;
    as_of?: string;
    metric_version?: string;
    coverage?: Record<string, unknown>;
  };
  errors: { code: string; message: string }[];
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8080";

function token(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("suffai_token");
}

export async function apiGet<T>(path: string): Promise<Envelope<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  const res = await fetch(`${API_BASE}${path}`, { headers, cache: "no-store" });
  return res.json();
}

export async function apiPost<T>(path: string, body: unknown): Promise<Envelope<T>> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    cache: "no-store",
  });
  return res.json();
}

export function setSession(accessToken: string, user: unknown) {
  window.localStorage.setItem("suffai_token", accessToken);
  window.localStorage.setItem("suffai_user", JSON.stringify(user));
}

export function clearSession() {
  window.localStorage.removeItem("suffai_token");
  window.localStorage.removeItem("suffai_user");
}

export function getUser<T = { brand: string; brand_id: string; email: string }>(): T | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem("suffai_user");
  return raw ? (JSON.parse(raw) as T) : null;
}

export { API_BASE };
