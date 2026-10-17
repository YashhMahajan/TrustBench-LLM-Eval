import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_URL, KEY_COOKIE } from "@/lib/api";
import { Mark } from "@/components/Icons";

async function signIn(formData: FormData) {
  "use server";
  const key = String(formData.get("key") ?? "").trim();
  let state = "bad";
  try {
    const res = await fetch(`${API_URL}/v1/me`, { headers: { Authorization: `Bearer ${key}` }, cache: "no-store" });
    state = res.ok ? "ok" : "bad";
  } catch {
    state = "down";
  }
  if (state === "ok") {
    (await cookies()).set(KEY_COOKIE, key, { httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 60 * 24 * 7 });
    redirect("/");
  }
  redirect(`/login?error=${state}`);
}

export const metadata = { title: "Sign in" };

export default async function Login({ searchParams }: { searchParams: Promise<{ error?: string; expired?: string }> }) {
  const sp = await searchParams;
  const message = sp.error === "bad" ? "This key was not accepted. Check that you copied all of it."
    : sp.error === "down" ? `Cannot reach the TrustBench server at ${API_URL}. Start it with: trustbench serve`
    : sp.expired ? "Your key stopped working. It may be revoked. Paste a new key." : null;
  return (
    <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 20 }}>
      <form action={signIn} className="panel rise" style={{ width: "min(440px, 100%)", padding: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <Mark size={34} /><h1 style={{ fontSize: 26 }}>TrustBench</h1>
        </div>
        <p style={{ margin: "0 0 18px", color: "var(--ink-2)" }}>Evidence for AI reliability. Paste an API key to open your workspace.</p>
        <label htmlFor="key" className="panel__title" style={{ display: "block", marginBottom: 8 }}>API key</label>
        <input id="key" name="key" className="input" placeholder="tb_…" autoComplete="off" spellCheck={false} required autoFocus />
        {message && <p role="alert" style={{ color: "var(--fail)", margin: "12px 0 0", fontSize: 13 }}>{message}</p>}
        <button className="btn btn--primary" style={{ width: "100%", marginTop: 16, minHeight: 42 }}>Open workspace</button>
        <p className="hint" style={{ margin: "16px 0 0" }}>
          No key yet? Run <code className="mono">trustbench demo</code> for sample data, or <code className="mono">trustbench key create</code>. The key stays on the server side of this app and is never sent to your browser's scripts.
        </p>
      </form>
    </div>
  );
}
