"use client";

import { FormEvent, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { apiPost } from "@/lib/api";

type Msg = { role: "user" | "assistant"; content: string };

export default function ChatPage() {
  const [msgs, setMsgs] = useState<Msg[]>([
    {
      role: "assistant",
      content:
        "Ask about geo active customers, commodity popularity, RFM mix, or brand coverage. I refuse market-share claims without verified garments.",
    },
  ]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    const query = q.trim();
    setQ("");
    setMsgs((m) => [...m, { role: "user", content: query }]);
    setBusy(true);
    try {
      const res = await apiPost<{
        answer: string;
        tool?: string | null;
      }>("/v1/chat/query", { query });
      const answer = res.data?.answer || res.errors?.[0]?.message || "No response";
      const tool = res.data?.tool ? `\n\nSource tool: ${res.data.tool}` : "";
      setMsgs((m) => [...m, { role: "assistant", content: answer + tool }]);
    } catch {
      setMsgs((m) => [...m, { role: "assistant", content: "API error." }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <RequireAuth>
      <div className="topbar">
        <div>
          <h1>Ask Suffai</h1>
          <p className="sub">
            Governed analytics tools only — no generated SQL, no PII, no unrestricted BigQuery.
          </p>
        </div>
      </div>
      <div className="panel">
        <div className="chat-log">
          {msgs.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>{m.content}</div>
          ))}
        </div>
        <form onSubmit={onSubmit} style={{ display: "flex", gap: "0.6rem" }}>
          <input
            style={{ flex: 1, border: "1px solid var(--line)", borderRadius: 999, padding: "0.75rem 1rem" }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. Which commodities are popular in PIN 560103?"
          />
          <button className="btn" type="submit" disabled={busy}>{busy ? "…" : "Ask"}</button>
        </form>
      </div>
    </RequireAuth>
  );
}
