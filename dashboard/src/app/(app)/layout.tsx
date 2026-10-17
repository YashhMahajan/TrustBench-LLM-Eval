import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_URL, KEY_COOKIE, Unreachable, api } from "@/lib/api";
import type { Me } from "@/lib/types";
import { Shell } from "@/components/Shell";

async function logout() {
  "use server";
  (await cookies()).delete(KEY_COOKIE);
  redirect("/login");
}

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  let me: Me;
  try {
    me = await api<Me>("/v1/me");
  } catch (e) {
    if (!(e instanceof Unreachable)) throw e;
    return (
      <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 20 }}>
        <div className="panel" style={{ maxWidth: 520, padding: 28 }}>
          <h1 style={{ fontSize: 24, marginBottom: 10 }}>The server is not answering</h1>
          <p style={{ color: "var(--ink-2)", margin: "0 0 14px" }}>The dashboard cannot reach <span className="mono">{API_URL}</span>. Start the server in a terminal, then reload this page:</p>
          <pre className="mono" style={{ background: "var(--surface-2)", border: "1px solid var(--rule)", borderRadius: 6, padding: 12, margin: 0 }}>trustbench serve</pre>
          <p className="hint" style={{ margin: "14px 0 0" }}>The server is somewhere else? Set <span className="mono">TRUSTBENCH_API_URL</span> before you start the dashboard.</p>
        </div>
      </div>
    );
  }
  return <Shell workspace={me.workspace.name} role={me.role} logout={logout}>{children}</Shell>;
}
