"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { I, Mark } from "./Icons";

const GROUPS = [
  { name: "Evaluate", items: [
    { href: "/", label: "Overview", icon: I.overview },
    { href: "/runs", label: "Experiments", icon: I.runs },
    { href: "/experiments", label: "Variant history", icon: I.runs },
    { href: "/regression", label: "Compare and gate", icon: I.gate },
    { href: "/benchmarks", label: "Benchmarks", icon: I.data },
    { href: "/models", label: "Models", icon: I.models },
  ] },
  { name: "Production", items: [{ href: "/monitor", label: "Monitor", icon: I.monitor }] },
  { name: "Workspace", items: [{ href: "/settings", label: "Settings", icon: I.settings }] },
];

function setTheme(t: "light" | "dark") {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("tb-theme", t); } catch { /* A private window may refuse. The theme still changes for this visit. */ }
}

export function Shell({ workspace, role, logout, children }: { workspace: string; role: string; logout: () => Promise<void>; children: ReactNode }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  const active = (href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(href + "/"));

  return (
    <div className="shell">
      <div className="topbar">
        <button className="foot-btn" aria-label="Open the menu" onClick={() => setOpen(true)}>{I.menu}</button>
        <span className="display" style={{ fontSize: 17 }}>TrustBench</span>
      </div>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside className="rail-nav" data-open={open}>
        <div className="brand"><Mark /> TrustBench</div>
        <div className="ws-chip">Workspace<strong>{workspace}</strong></div>
        <nav aria-label="Main">
          {GROUPS.map((g) => (
            <div key={g.name}>
              <div className="nav-group">{g.name}</div>
              {g.items.map((it) => (
                <Link key={it.href} href={it.href} className="nav-link" aria-current={active(it.href) ? "page" : undefined}>{it.icon}{it.label}</Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="rail-foot">
          <button className="foot-btn" onClick={() => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")}>{I.sun}Switch theme</button>
          <form action={logout}><button className="foot-btn" style={{ width: "100%" }}>{I.logout}Sign out<span className="chip" style={{ marginLeft: "auto", background: "transparent", color: "var(--rail-muted)", borderColor: "var(--rail-line)" }}>{role}</span></button></form>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
