import Link from "next/link";
import { api, apiOrNull } from "@/lib/api";
import { VERDICT_TEXT, ago, label, ms, pct, pp, shortId, usd } from "@/lib/format";
import type { Baseline, Compare, Decision, Delta, RunRow } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { Stamp } from "@/components/Stamp";
import { DeltaRail } from "@/components/DeltaRail";

export const metadata = { title: "Compare and gate" };
type SP = { c?: string; b?: string; min?: string };

const SENTENCE: Record<string, string> = {
  meaningful_improvement: "better than", improvement_below_threshold: "slightly better than, but maybe not by enough to matter,",
  equivalent: "not different in a way that matters from", inconclusive: "not clearly better or worse than",
  regression_below_threshold: "slightly worse than, but maybe not by enough to matter,", meaningful_regression: "worse than",
};

export default async function Regression({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const [{ runs }, { baselines }, { decisions }] = await Promise.all([
    api<{ runs: RunRow[] }>("/v1/runs?limit=40"),
    api<{ baselines: Baseline[] }>("/v1/baselines"),
    api<{ decisions: Decision[] }>("/v1/decisions?limit=15"),
  ]);
  const options = runs.flatMap((r) => Object.keys(r.models).map((m) => ({ value: `${r.id}|${m}`, text: `${r.name ?? shortId(r.id)} · ${m.replace("demo:", "")}` })));
  const first = baselines[0];
  // Start with the run that was gated last: that is the one a person wants to understand.
  const gated = decisions.find((d) => !first || d.run_id !== first.run_id);
  const defC = gated ? `${gated.run_id}|${gated.model}` : options.find((o) => !first || !o.value.startsWith(first.run_id))?.value ?? options[0]?.value;
  const defB = first ? `@${first.name}` : options[1]?.value;
  const c = sp.c ?? defC;
  const b = sp.b ?? defB;
  const min = Math.min(0.2, Math.max(0, Number(sp.min ?? 0.01) || 0.01));

  let cmp: Compare | null = null;
  let problem: string | null = null;
  if (c && b) {
    const [cRun, cModel] = c.split("|");
    const baseParam = b.startsWith("@") ? `baseline=${encodeURIComponent(b.slice(1))}` : `baseline=${encodeURIComponent(b.split("|")[0])}&baseline_model=${encodeURIComponent(b.split("|")[1] ?? "")}`;
    try {
      cmp = await api<Compare>(`/v1/compare?candidate=${encodeURIComponent(cRun)}&model=${encodeURIComponent(cModel ?? "")}&${baseParam}&min_effect=${min}`);
    } catch (e) {
      problem = e instanceof Error ? e.message : "The comparison failed.";
    }
  }

  return (
    <>
      <PageHead title="Compare and gate" sub="Is the new version better, the same, or worse? We compare the same cases in both runs, so luck in the choice of cases does not decide." />
      <Panel delay={20}>
        <form method="get" style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "flex-end" }}>
          <Field name="c" title="Candidate (the new version)" value={c} options={options} />
          <Field name="b" title="Baseline (what works today)" value={b} options={[...baselines.map((x) => ({ value: `@${x.name}`, text: `baseline “${x.name}”` })), ...options]} />
          <label style={{ display: "grid", gap: 6 }}>
            <span className="panel__title">A change counts from</span>
            <select name="min" className="select" defaultValue={String(min)}>{[0.005, 0.01, 0.02, 0.05].map((v) => <option key={v} value={v}>{pct(v)} pass rate</option>)}</select>
          </label>
          <button className="btn btn--primary">Compare</button>
        </form>
      </Panel>

      {problem && <div className="callout callout--warn" style={{ marginTop: 16 }}>{problem}</div>}
      {cmp && <Result cmp={cmp} />}

      <div className="grid-main" style={{ marginTop: 16 }}>
        <Panel title="Release decisions" delay={100} flush id="decisions">
          {decisions.length === 0 ? <Empty title="No decision yet">Check a run against a policy: <code>trustbench gate check RUN_ID</code></Empty> :
            decisions.map((d) => (
              <details key={d.id} className="decision">
                <summary className="rowlink" style={{ cursor: "pointer", alignItems: "center", listStyle: "none" }}>
                  <Stamp status={d.status} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ fontWeight: 500 }}>{runName(runs, d.run_id)}</span> <span className="hint mono">{d.model}</span>
                    <div className="hint">policy “{d.policy}”{d.baseline_run_id && ` · against ${runName(runs, d.baseline_run_id)}`} · {ago(d.at)}</div>
                  </div>
                  <span className="hint mono">{d.checks.filter((x) => x.status === "pass").length}/{d.checks.length} rules met</span>
                </summary>
                <div style={{ padding: "4px 18px 16px 18px", display: "grid", gap: 8 }}>
                  {d.checks.map((k) => (
                    <div key={k.rule} style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 10, alignItems: "baseline" }}>
                      <Stamp status={k.status} />
                      <div><span className="mono" style={{ fontWeight: 500 }}>{k.rule.replace(/_/g, " ")}</span><div className="hint">needs {k.required}</div><div className="hint mono">{k.observed}</div>{k.evidence && <div className="hint" style={{ color: "var(--ink-2)" }}>{k.evidence}</div>}</div>
                    </div>
                  ))}
                  {d.warnings.map((w) => <div key={w} className="callout callout--warn">{w}</div>)}
                </div>
              </details>
            ))}
        </Panel>
        <Panel title="Baselines" delay={140} flush>
          {baselines.length === 0 ? <Empty title="No baseline">Name a run you trust: <code>trustbench baseline set main RUN_ID</code></Empty> :
            baselines.map((x) => (
              <Link key={x.name} href={`/runs/${x.run_id}`} className="rowlink">
                <span className="dot dot--info" />
                <span><span style={{ fontWeight: 500 }}>{x.name}</span><span className="hint mono" style={{ display: "block" }}>{runName(runs, x.run_id)}{x.model && ` · ${x.model}`} · set {ago(x.set_at)}</span></span>
              </Link>
            ))}
        </Panel>
      </div>
    </>
  );
}

function Field({ name, title, value, options }: { name: string; title: string; value?: string; options: { value: string; text: string }[] }) {
  return (
    <label style={{ display: "grid", gap: 6, flex: "1 1 220px", minWidth: 0 }}>
      <span className="panel__title">{title}</span>
      <select name={name} className="select" defaultValue={value}>{options.map((o) => <option key={o.value} value={o.value}>{o.text}</option>)}</select>
    </label>
  );
}

const runName = (runs: RunRow[], id: string) => runs.find((r) => r.id === id)?.name ?? shortId(id);

function Result({ cmp }: { cmp: Compare }) {
  const o = cmp.comparison.overall;
  const v = VERDICT_TEXT[o.verdict];
  const rows: Delta[] = [o, ...Object.values(cmp.comparison.by_task).sort((a, b) => (a.diff ?? 0) - (b.diff ?? 0))];
  const ob = cmp.operations.baseline, oc = cmp.operations.candidate;
  const worse = (a: number | null, b: number | null) => (a != null && b != null ? (b > a * 1.1 ? "fail" : b < a * 0.9 ? "pass" : "") : "");
  return (
    <div style={{ display: "grid", gap: 16, marginTop: 16 }}>
      <Panel delay={50}>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
          <span className={`stamp stamp--lg stamp--${v.tone === "pass" ? "pass" : v.tone === "fail" ? "fail" : v.tone === "warn" ? "inconclusive" : "incomplete"}`}>{v.text}</span>
          <h2 style={{ fontSize: "clamp(1.1rem, 2vw, 1.4rem)", lineHeight: 1.25, flex: "1 1 320px" }}>
            <span className="mono" style={{ color: "var(--accent-strong)" }}>{cmp.candidate.model}</span> is {SENTENCE[o.verdict]} <span className="mono" style={{ color: "var(--accent-strong)" }}>{cmp.baseline.model}</span>: {pp(o.diff)}.
          </h2>
        </div>
        <p className="hint" style={{ margin: "10px 0 0" }}>
          {o.n} cases answered in both runs. The 95% interval of the change is {o.ci ? `${pp(o.ci[0])} to ${pp(o.ci[1])}` : "unknown"}. {o.method === "mcnemar" ? `The candidate fixed ${o.candidate_only_pass} case(s) and broke ${o.baseline_only_pass}.` : ""} The hatched band marks changes smaller than {pct(cmp.min_effect)}: too small to matter.
        </p>
        {cmp.comparison.warnings.map((w) => <div key={w} className="callout callout--warn" style={{ marginTop: 10 }}>{w}</div>)}
      </Panel>

      <Panel title="Change by task" delay={90} flush>
        <div className="scroll-x"><table className="tbl">
          <thead><tr><th>Task</th><th>Cases</th><th>Baseline</th><th>Candidate</th><th style={{ width: "34%" }}>Change and 95% interval</th><th>Verdict</th></tr></thead>
          <tbody>{rows.map((d, i) => {
            const vt = VERDICT_TEXT[d.verdict];
            return (
              <tr key={d.label} style={i === 0 ? { background: "var(--surface-2)" } : undefined}>
                <td style={{ fontWeight: i === 0 ? 650 : 500 }}>{i === 0 ? "All tasks" : label(d.label)}</td>
                <td className="mono">{d.n}</td><td className="mono">{pct(d.baseline, 0)}</td><td className="mono">{pct(d.candidate, 0)}</td>
                <td><DeltaRail diff={d.diff} ci={d.ci} minEffect={cmp.min_effect} tone={vt.tone} delay={i * 30} /></td>
                <td><span className={`res res--${vt.tone === "pass" ? "pass" : vt.tone === "fail" ? "fail" : "skipped"}`} style={{ whiteSpace: "nowrap" }}>{vt.text}</span></td>
              </tr>
            );
          })}</tbody>
        </table></div>
      </Panel>

      <div className="grid-main">
        <Panel title="Areas that got worse" delay={120} aside={<span className="hint">false discovery controlled</span>} flush>
          {cmp.worse_slices.length === 0 ? <Empty title="No area got clearly worse">Or the groups are too small to test.</Empty> :
            cmp.worse_slices.slice(0, 8).map((s) => (
              <div key={s.dimension + s.value} className="rowlink" style={{ alignItems: "center" }}>
                <span className="dot dot--fail" style={{ marginTop: 0 }} />
                <div style={{ flex: 1 }}><span style={{ fontWeight: 500 }}>{label(s.dimension)} = {label(s.value)}</span><div className="hint">{s.n} cases · p = {s.p_value?.toFixed(4)}</div></div>
                <div className="mono">{pct(s.baseline, 0)} → {pct(s.candidate, 0)}</div>
              </div>
            ))}
        </Panel>
        <Panel title="Speed, errors and cost" delay={150}>
          <dl className="kv">
            <dt>Latency P95</dt><dd className={worse(ob.latency_ms["95"], oc.latency_ms["95"])}>{ms(ob.latency_ms["95"])} → {ms(oc.latency_ms["95"])}</dd>
            <dt>Calls that failed</dt><dd>{pct(ob.call_failure_rate)} → {pct(oc.call_failure_rate)}</dd>
            <dt>Cost per success</dt><dd>{usd(ob.cost_per_success_usd)} → {usd(oc.cost_per_success_usd)}</dd>
          </dl>
        </Panel>
      </div>
    </div>
  );
}
