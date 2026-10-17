import { api } from "@/lib/api";
import { ago, label } from "@/lib/format";
import type { AuditEntry, Me, Settings as S } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { Notice } from "@/components/Notice";
import { saveSettings } from "./actions";

export const metadata = { title: "Settings" };

export default async function Settings({ searchParams }: { searchParams: Promise<{ error?: string; done?: string }> }) {
  const sp = await searchParams;
  const [me, s] = await Promise.all([api<Me>("/v1/me"), api<S>("/v1/settings")]);
  const admin = me.role === "admin";
  const audit = admin ? await api<{ entries: AuditEntry[] }>("/v1/audit") : { entries: [] };
  return (
    <>
      <PageHead title="Settings" sub={<>Workspace <strong>{me.workspace.name}</strong>. You are signed in with a key that can <strong>{me.role}</strong>. {admin ? "" : "Only an admin key can change these settings."}</>} />
      <Notice error={sp.error} done={sp.done} />
      <div className="grid-main">
        <Panel title="What we keep of your production traffic" delay={20}>
          <form action={saveSettings} style={{ display: "grid", gap: 20 }}>
            <label className="check"><input type="checkbox" name="store_content" defaultChecked={s.store_content} disabled={!admin} />
              <span><strong>Keep the text of inputs and outputs</strong><span className="hint" style={{ display: "block" }}>Off by default: we then store only measurements (time, size, tokens, cost, status). When on, text is redacted and encrypted before it is stored. Needs an encryption key on the server. Without stored text, a failed trace cannot become a test.</span></span></label>
            <label className="check"><input type="checkbox" name="redact" defaultChecked={s.redact} disabled={!admin} />
              <span><strong>Remove personal data and secrets first</strong><span className="hint" style={{ display: "block" }}>Emails, phone numbers, cards, keys and tokens become markers such as [EMAIL]. It finds patterns. It does not find names or addresses.</span></span></label>
            <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
              <label style={{ display: "grid", gap: 6 }}><span className="panel__title">Delete traces after (days)</span><input className="input" type="number" min={1} max={3650} name="retention_days" defaultValue={s.retention_days} disabled={!admin} style={{ width: 160 }} /></label>
              <label style={{ display: "grid", gap: 6 }}><span className="panel__title">Share checked online (%)</span><input className="input" type="number" min={0} max={100} step={1} name="sample_rate" defaultValue={Math.round(s.sample_rate * 100)} disabled={!admin} style={{ width: 160 }} /></label>
            </div>
            {admin && <div><button className="btn btn--primary">Save settings</button></div>}
          </form>
        </Panel>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <Panel title="Online checks" delay={60}>
            {s.online_evaluators.length === 0 ? <p className="hint" style={{ margin: 0 }}>No online check is set. Set them with the API: <span className="mono">PUT /v1/settings</span> with <span className="mono">online_evaluators</span>.</p> : (
              <><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{s.online_evaluators.map((e, i) => <span key={i} className="chip">{label(e.type)}</span>)}</div>
                {s.online_judge && <p className="hint" style={{ margin: "10px 0 0" }}>Judge model: <span className="mono">{s.online_judge}</span></p>}</>
            )}
          </Panel>
          <Panel title="Keys" delay={90}><p className="hint" style={{ margin: 0 }}>Keys are made and revoked in a terminal, so a stolen browser session cannot mint new ones: <span className="mono">trustbench key create / list / revoke</span>.</p></Panel>
        </div>
      </div>
      {admin && (
        <Panel title="Audit log" delay={110} aside={<span className="hint">who did something sensitive</span>} flush>
          {audit.entries.length === 0 ? <Empty title="Nothing logged yet">Reading stored text, deleting a trace and changing settings are logged here.</Empty> : (
            <div className="scroll-x"><table className="tbl"><thead><tr><th>When</th><th>Action</th><th>Target</th><th>Key</th></tr></thead>
              <tbody>{audit.entries.slice(0, 20).map((e) => <tr key={e.id}><td className="mono">{ago(e.at)}</td><td className="mono">{e.action}</td><td className="mono hint">{e.target || "–"}</td><td className="mono">{e.key_id ?? "–"}</td></tr>)}</tbody></table></div>
          )}
        </Panel>
      )}
    </>
  );
}
