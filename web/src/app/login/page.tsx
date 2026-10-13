"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, apiPost, setSession, getUser } from "@/lib/api";
import {
  AuthConfig,
  exchangeIdentitySession,
  signInWithIdentityPassword,
} from "@/lib/identity";

export default function LoginPage() {
  const router = useRouter();
  const [brands, setBrands] = useState<string[]>([]);
  const [brand, setBrand] = useState("Peter England");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [mode, setMode] = useState<"demo" | "identity">("demo");
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (getUser()) router.replace("/geo");
    apiGet<{ brands: string[] }>("/v1/auth/brands")
      .then((res) => {
        const list = res.data?.brands || [];
        setBrands(list);
        if (list.length) setBrand(list[0]);
      })
      .catch(() => setError("API unreachable. Start suffai-api on :8080."));
    apiGet<AuthConfig>("/v1/auth/config")
      .then((res) => {
        const cfg = res.data;
        if (!cfg) return;
        setAuthConfig(cfg);
        if (cfg.identity_platform_enabled && cfg.identity_web_api_key) {
          setMode("identity");
        } else if (cfg.demo_auth_enabled) {
          setMode("demo");
        }
      })
      .catch(() => undefined);
  }, [router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      if (mode === "identity") {
        const apiKey =
          authConfig?.identity_web_api_key ||
          process.env.NEXT_PUBLIC_IDENTITY_API_KEY ||
          "";
        if (!apiKey) {
          setError("Identity Platform enabled but web API key missing.");
          setLoading(false);
          return;
        }
        const { idToken } = await signInWithIdentityPassword(email, password, apiKey);
        await exchangeIdentitySession(idToken);
        router.replace("/geo");
        return;
      }
      const res = await apiPost<{
        access_token: string;
        user: unknown;
      }>("/v1/auth/brand/login", { brand, password });
      if (res.errors?.length) {
        setError(res.errors[0].message);
        setLoading(false);
        return;
      }
      setSession(res.data.access_token, res.data.user);
      router.replace("/geo");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed — check API_URL / CORS.");
      setLoading(false);
    }
  }

  const identityReady = Boolean(
    authConfig?.identity_platform_enabled &&
      (authConfig?.identity_web_api_key || process.env.NEXT_PUBLIC_IDENTITY_API_KEY)
  );

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Suffai</h1>
        <p className="sub">Brand Intelligence on Google Cloud. Tenant-scoped, privacy-preserving.</p>

        {identityReady && (
          <div className="field" style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              className={`btn ${mode === "identity" ? "" : "secondary"}`}
              onClick={() => setMode("identity")}
              style={{ flex: 1 }}
            >
              Identity Platform
            </button>
            {authConfig?.demo_auth_enabled && (
              <button
                type="button"
                className={`btn ${mode === "demo" ? "" : "secondary"}`}
                onClick={() => setMode("demo")}
                style={{ flex: 1 }}
              >
                Demo brand
              </button>
            )}
          </div>
        )}

        {mode === "identity" ? (
          <>
            <div className="field">
              <label htmlFor="email">Work email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="analyst@brand.com"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
          </>
        ) : (
          <>
            <div className="field">
              <label htmlFor="brand">Brand account</label>
              <select id="brand" value={brand} onChange={(e) => setBrand(e.target.value)}>
                {brands.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="brand123"
                required
              />
            </div>
          </>
        )}

        {error && <div className="alert danger">{error}</div>}
        <button className="btn" type="submit" disabled={loading} style={{ width: "100%", marginTop: "0.5rem" }}>
          {loading ? "Signing in…" : "Enter workspace"}
        </button>
        <p className="sub" style={{ marginTop: "1rem", fontSize: "0.85rem" }}>
          {mode === "identity"
            ? "Google Identity Platform (Firebase Auth). Tenant map from custom claims / IDENTITY_TENANT_MAP_JSON."
            : (
              <>
                Demo password: <strong>brand123</strong>. Prod uses Identity Platform.
              </>
            )}
        </p>
      </form>
    </div>
  );
}
