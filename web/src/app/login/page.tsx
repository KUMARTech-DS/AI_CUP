"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, apiPost, setSession, getUser } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [brands, setBrands] = useState<string[]>([]);
  const [brand, setBrand] = useState("Peter England");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (getUser()) router.replace("/geo");
    apiGet<{ brands: string[] }>("/v1/auth/brands").then((res) => {
      const list = res.data?.brands || [];
      setBrands(list);
      if (list.length) setBrand(list[0]);
    }).catch(() => setError("API unreachable. Start suffai-api on :8080."));
  }, [router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
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
    } catch {
      setError("Login failed — check API_URL / CORS.");
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Suffai</h1>
        <p className="sub">Brand Intelligence on Google Cloud. Tenant-scoped, privacy-preserving.</p>
        <div className="field">
          <label htmlFor="brand">Brand account</label>
          <select id="brand" value={brand} onChange={(e) => setBrand(e.target.value)}>
            {brands.map((b) => (
              <option key={b} value={b}>{b}</option>
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
        {error && <div className="alert danger">{error}</div>}
        <button className="btn" type="submit" disabled={loading} style={{ width: "100%", marginTop: "0.5rem" }}>
          {loading ? "Signing in…" : "Enter workspace"}
        </button>
        <p className="sub" style={{ marginTop: "1rem", fontSize: "0.85rem" }}>
          Demo password: <strong>brand123</strong>. Auth swaps to Identity Platform in prod.
        </p>
      </form>
    </div>
  );
}
