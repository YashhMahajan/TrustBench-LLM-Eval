import { api } from "@/lib/api";
import { label, pct, pp } from "@/lib/format";
import type { RateK, Robustness as R } from "@/lib/types";
import { Empty, Panel } from "@/components/Panel";
import { Rail } from "@/components/Rail";

const rate = (r: RateK | null) => (r && r.n ? r.k / r.n : null);

export async function Robustness({ id, model }: { id: string; model: string }) {
  const r = await api<R>(`/v1/runs/${id}/robustness?model=${encodeURIComponent(model)}`);
  if (r.pairs === 0) {
    return <Panel><Empty title="This run has no changed copies of cases">Make them with <code>trustbench robustness build grounded</code>, run the new file, then look here.</Empty></Panel>;
  }
  const measures: [string, RateK | null, string, boolean][] = [
    ["Break rate", r.break_rate, "Passed the original but failed the changed copy. Lower is better.", true],
    ["Consistency", r.consistency, "Same result for the original and its copy. Higher is better, but a model can be consistently wrong.", false],
    ["Consistently wrong", r.consistently_wrong, "Failed both the original and the copy.", true],
    ["Fix rate", r.fix_rate, "Failed the original but passed the copy: luck, or noise.", false],
  ];
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="grid-main">
        <Panel title={`${r.pairs} pairs of original and changed case`} delay={20}>
          <div style={{ display: "grid", gap: 18 }}>
            {measures.map(([name, v, hint, bad], i) => (
              <div key={name}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><strong>{name}</strong><span className="hint mono">{v ? `${v.k} of ${v.n}` : "–"}</span></div>
                <Rail rate={rate(v)} ci={v?.ci ?? null} bad={bad && (rate(v) ?? 0) > 0.15} delay={i * 40} compact />
                <div className="hint" style={{ marginTop: 2 }}>{hint}</div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Pass rate, original against copy" delay={60}>
          <dl className="kv" style={{ marginBottom: 14 }}>
            <dt>Originals</dt><dd>{pct(r.original_rate)}</dd><dt>Changed copies</dt><dd>{pct(r.variant_rate)}</dd>
            <dt>Degradation</dt><dd>{pp(r.degradation)}</dd>
          </dl>
          {r.degradation_ci && <p className="hint" style={{ margin: 0 }}>95% interval of the degradation: {pp(r.degradation_ci[0])} to {pp(r.degradation_ci[1])}.</p>}
          {r.worst_transform && <p style={{ margin: "12px 0 0" }}>Weakest change: <strong>{label(r.worst_transform.name)}</strong> ({pct(r.worst_transform.variant_rate, 0)} pass).</p>}
          {r.worst_task && <p style={{ margin: "4px 0 0" }}>Weakest task: <strong>{label(r.worst_task.name)}</strong> ({pct(r.worst_task.variant_rate, 0)} pass).</p>}
        </Panel>
      </div>
      <Panel title="By kind of change" delay={100} flush>
        <div className="scroll-x"><table className="tbl">
          <thead><tr><th>Change</th><th>Pairs</th><th>Original</th><th style={{ width: "40%" }}>Break rate</th></tr></thead>
          <tbody>{r.by_transform.map((g, i) => (
            <tr key={g.name}><td style={{ fontWeight: 500 }}>{label(g.name)}</td><td className="mono">{g.pairs}</td><td className="mono">{pct(g.original_rate, 0)} → {pct(g.variant_rate, 0)}</td>
              <td><Rail rate={rate(g.break_rate)} ci={g.break_rate.ci} n={g.break_rate.n} bad={(rate(g.break_rate) ?? 0) > 0.15} delay={i * 30} /></td></tr>
          ))}</tbody>
        </table></div>
      </Panel>
      {Object.keys(r.fragile_cases).length > 0 && (
        <Panel title="Cases that broke" delay={140}>
          <div style={{ display: "grid", gap: 6 }}>{Object.entries(r.fragile_cases).slice(0, 14).map(([c, ts]) => (
            <div key={c}><span className="mono" style={{ fontWeight: 500 }}>{c}</span> <span className="hint">broke under {ts.map(label).join(", ")}</span></div>
          ))}</div>
        </Panel>
      )}
      {r.warnings.map((w) => <div key={w} className="callout callout--warn">{w}</div>)}
    </div>
  );
}
