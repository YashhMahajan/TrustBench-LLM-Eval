import { api } from "@/lib/api";
import { label, pct, usd } from "@/lib/format";
import type { Candidate, RunRow } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { Frontier } from "@/components/Frontier";
import { Rail } from "@/components/Rail";

export const metadata = { title: "Models" };
type SP = { runs?: string; q?: string; cost?: string; p95?: string };

export default async function Models({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const { runs } = await api<{ runs: RunRow[] }>("/v1/runs?limit=40");
  const multi = runs.filter((r) => Object.keys(r.models).length > 1)[0];
  const chosen = (sp.runs ? sp.runs.split(",") : [multi?.id ?? runs[0]?.id]).filter(Boolean);
  const params = new URLSearchParams({ runs: chosen.join(",") });
  (["q", "cost", "p95"] as const).forEach((k) => sp[k] && params.set({ q: "min_quality", cost: "max_cost", p95: "max_p95" }[k], sp[k]!));
  const data = chosen.length ? await api<{ candidates: Candidate[] }>(`/v1/frontier?${params}`) : { candidates: [] };
  const c = data.candidates;
  const limited = !!(sp.q || sp.cost || sp.p95);

  return (
    <>
      <PageHead title="Models" sub="Quality, cost and speed together. A model is worth considering only if no other model is at least as good on all three. Models on the dashed line pass that test." />
      <Panel delay={20}>
        <form method="get" style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "grid", gap: 6, flex: "1 1 220px" }}><span className="panel__title">Runs to compare (same dataset)</span>
            <select name="runs" className="select" defaultValue={chosen.join(",")}>
              {runs.map((r) => <option key={r.id} value={r.id}>{r.name ?? r.id} · {Object.keys(r.models).length} model(s)</option>)}
              {runs.length > 1 && <option value={runs.map((r) => r.id).join(",")}>All runs together</option>}
            </select></label>
          <label style={{ display: "grid", gap: 6 }}><span className="panel__title">Pass rate at least</span><input name="q" className="input" style={{ width: 110 }} placeholder="0.85" defaultValue={sp.q} inputMode="decimal" /></label>
          <label style={{ display: "grid", gap: 6 }}><span className="panel__title">Cost per success at most ($)</span><input name="cost" className="input" style={{ width: 130 }} placeholder="0.005" defaultValue={sp.cost} inputMode="decimal" /></label>
          <label style={{ display: "grid", gap: 6 }}><span className="panel__title">P95 latency at most (s)</span><input name="p95" className="input" style={{ width: 110 }} placeholder="2" defaultValue={sp.p95} inputMode="decimal" /></label>
          <button className="btn btn--primary">Show</button>
        </form>
      </Panel>
      {c.length === 0 ? <div style={{ marginTop: 16 }}><Panel><Empty title="Nothing to compare yet">Run a dataset with several models: <code>trustbench run --model a:x --model b:y</code></Empty></Panel></div> : (
        <div style={{ display: "grid", gap: 16, marginTop: 16 }}>
          <Panel title="Quality against cost" delay={50} aside={<span className="hint">bars: 95% interval of the pass rate</span>}><Frontier items={c} /></Panel>
          <Panel title="Details" delay={90} flush>
            <div className="scroll-x"><table className="tbl">
              <thead><tr><th>Model</th><th style={{ width: "30%" }}>Pass rate</th><th>Cost per success</th><th>P95 latency</th><th>{limited ? "Meets your limits" : "Worth considering?"}</th></tr></thead>
              <tbody>{[...c].sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0)).map((m, i) => (
                <tr key={m.name} style={{ opacity: m.feasible === false ? 0.6 : 1 }}>
                  <td className="mono" style={{ fontWeight: 500 }}>{m.name}</td>
                  <td><Rail rate={m.quality} ci={m.quality_ci} n={m.cases} delay={i * 40} /></td>
                  <td className="mono">{usd(m.cost_per_success)}</td><td className="mono">{m.p95_seconds == null ? "–" : `${m.p95_seconds.toFixed(2)} s`}</td>
                  <td style={{ maxWidth: 280 }}>
                    {m.unknown.length ? <span className="hint">Cannot compare: {m.unknown.join(", ")} unknown</span>
                      : limited ? (m.feasible ? <span className="res res--pass">yes</span> : <span className="hint">{m.why_not_feasible.join("; ")}</span>)
                      : m.on_frontier ? <span className="res res--pass">yes</span> : <span className="hint">Beaten by {m.dominated_by.map((d) => d.replace("demo:", "")).join(", ")}{m.quality_uncertain && ", but the quality gap may be noise"}</span>}
                  </td>
                </tr>
              ))}</tbody>
            </table></div>
          </Panel>
        </div>
      )}
    </>
  );
}
