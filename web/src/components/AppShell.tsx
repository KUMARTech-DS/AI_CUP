"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearSession, getUser } from "@/lib/api";

const NAV = [
  { href: "/geo", label: "Geo Intelligence" },
  { href: "/brand", label: "Brand & Competition" },
  { href: "/research", label: "Product Research" },
  { href: "/campaigns", label: "Campaign Lab" },
  { href: "/activation", label: "Activation" },
  { href: "/capture", label: "Capture" },
  { href: "/review", label: "Review" },
  { href: "/chat", label: "Ask Suffai" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const user = getUser();

  function logout() {
    clearSession();
    router.replace("/login");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark">
          Suffai
          <span>Fashion Intelligence · GCP</span>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={pathname?.startsWith(item.href) ? "active" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div style={{ marginTop: "auto", fontSize: "0.85rem", opacity: 0.85 }}>
          <div>{user?.brand || "Brand tenant"}</div>
          <div style={{ opacity: 0.65, marginBottom: "0.75rem" }}>{user?.email}</div>
          <button className="btn secondary" onClick={logout} style={{ color: "#fff", borderColor: "#ffffff44" }}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
