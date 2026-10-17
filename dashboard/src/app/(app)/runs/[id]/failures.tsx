import Link from "next/link";
import { api } from "@/lib/api";
import { label, pct } from "@/lib/format";
import type { FailuresReport } from "@/lib/types";
import { Empty, Panel } from "@/components/Panel";
import { Bar } from "@/components/Controls";

export async function Failures({ id, model }: { id: string; model: string }) {
  const f = await api<FailuresReport>(`/v1/runs/${id}/failures?model=${encodeURIComponent(model)}`);
  if (!f.failed_cases) return <Panel><Empty title="No case failed">{f.cases_with_a_result} cases have a result and every one passed.</Empty></Panel>;
  const maxScore = Math.max(...f.priority.map((p) => p.score), 1);
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <p className="hint" style={{ margin: 0 }}>{f.failed_cases} of {f.cases_with_a_result} cases failed. One case can fail in more than one way.</p>
      <div className="grid-main">
        <Panel title="How the cases failed" delay={20} flush>
          {f.labels.map((l) => (
            <div key={l.label} className="rowlink" style={{ alignItems: "center" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><span style={{ fontWeight: 500 }}>{label(l.label)}</span><span className="mono">{l.cases} · {pct(l.share, 0)}</span></div>
                <Bar value={l.share} tone={l.label.includes("injection") || l.label.includes("contradicted") ? "fail" : "accent"} />
                <div className="hint" style={{ marginTop: 3 }}>{l.meaning}</div>
              </div>
              <Link className="btn btn--sm" href={`/runs/${id}?tab=cases&result=fail&q=${encodeURIComponent(l.case_ids[0])}`} scroll={false}>Open</Link>
            </div>
          ))}
        </Panel>
        <Panel title="What to fix first" delay={60} flush>
          <div className="scroll-x"><table className="tbl">
            <thead><tr><th>Failure</th><th>Sev.</th><th>Freq.</th><th>Score</th></tr></thead>
            <tbody>{f.priority.map((p) => (
              <tr key={p.name}><td>{label(p.name)}<div className="hint">{p.cases} cases</div></td><td className="mono">{p.severity} <span className="hint">×{p.weight}</span></td><td className="mono">{p.frequency_pct.toFixed(1)}%</td>
                <td style={{ minWidth: 70 }}><span className="mono" style={{ fontWeight: 600 }}>{p.score.toFixed(0)}</span><Bar value={p.score / maxScore} tone="warn" /></td></tr>
            ))}</tbody>
          </table></div>
          <p className="hint" style={{ margin: 0, padding: "10px 18px 14px" }}>Score = severity weight × frequency × exposure × detectability. A rare severe failure can outrank a frequent small one.</p>
        </Panel>
      </div>

      {f.clusters.length > 0 && (
        <Panel title="Recurring patterns" delay={100} aside={<span className="hint">patterns, not proven causes</span>}>
          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))" }}>
            {f.clusters.map((c) => (
              <div key={c.id} style={{ border: "1px solid var(--rule)", borderRadius: 8, padding: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}><strong className="display" style={{ fontSize: 16 }}>{c.size} cases</strong><span className="hint mono">{pct(c.share, 0)} of all</span></div>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", margin: "8px 0" }}>{c.terms.slice(0, 5).map((t) => <span key={t} className="chip">{t}</span>)}</div>
                <div className="hint">{Object.keys(c.tasks).map(label).join(", ")} · {Object.keys(c.labels).map(label).join(", ")}</div>
                <div className="hint mono" style={{ marginTop: 6 }}>{c.examples.join(", ")}</div>
                <div style={{ marginTop: 8, fontSize: 12.5 }}>{c.follow_up}</div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {Object.keys(f.rag_stages).length > 0 && (
        <Panel title="Where in the pipeline" delay={140}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {Object.entries(f.rag_stages).map(([k, v]) => <span key={k} className="chip">{label(k)} · {v}</span>)}
          </div>
          <p className="hint" style={{ margin: "10px 0 0" }}>Blames the first stage that failed, from the design of the case. It is a lead to check, not proof of the cause.</p>
        </Panel>
      )}
    </div>
  );
}
