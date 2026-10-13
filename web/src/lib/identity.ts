/**
 * Google Identity Platform / Firebase Auth REST adapter (no Firebase SDK required).
 * Uses Identity Toolkit password sign-in, then exchanges idToken with suffai-api.
 */

import { apiPost, setSession } from "./api";

export type AuthConfig = {
  demo_auth_enabled: boolean;
  identity_platform_enabled: boolean;
  identity_platform_project: string | null;
  identity_web_api_key: string | null;
};

export async function signInWithIdentityPassword(
  email: string,
  password: string,
  apiKey: string
): Promise<{ idToken: string; email: string }> {
  const url = `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${encodeURIComponent(apiKey)}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, returnSecureToken: true }),
  });
  const data = await res.json();
  if (!res.ok || data.error) {
    throw new Error(data?.error?.message || "Identity Platform sign-in failed");
  }
  return { idToken: data.idToken as string, email: data.email as string };
}

export async function exchangeIdentitySession(idToken: string) {
  const res = await apiPost<{
    access_token: string;
    user: unknown;
    auth_provider: string;
  }>("/v1/auth/identity/session", { id_token: idToken });
  if (res.errors?.length) {
    throw new Error(res.errors[0].message);
  }
  setSession(res.data.access_token, res.data.user);
  return res.data;
}
