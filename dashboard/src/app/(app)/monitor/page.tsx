import { api } from "@/lib/api";
import { ago, label, ms, pct, usd } from "@/lib/format";
import type { Drift, Flagged, Monitor as M, TraceRow } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { Kind, KindLegend } from "@/components/Stamp";
import { Rail } from "@/components/Rail";
import { Sparkbars } from "@/components/Sparkbars";
import { Segmented } from "@/components/Controls";
import { Notice } from "@/components/Notice";
import { promote } from "./actions";

export const metadata = { title: "Production monitor" };
type SP = { days?: string; name?: string; error?: string; done?: string };

export default async function Monitor({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const days = [1, 7, 30].includes(Number(sp.days)) ? Number(sp.days) : 7;
  const nameQ = sp.name ? `&name=${encodeURIComponent(sp.name)}` : "";
  const [mon, drift, { flagged }, { traces }, all] = await Promise.all([
    api<M>(`/v1/monitor?days=${days}${nameQ}`),
    api<Drift>(`/v1/drift?current_days=${days}&reference_days=${Math.min(120, days * 4)}${nameQ}`),
    api<{ flagged: Flagged[] }>(`/v1/flagged?limit=8${nameQ}`),
    api<{ traces: TraceRow[] }>(`/v1/traces?limit=12${nameQ}`),
    api<{ traces: TraceRow[] }>("/v1/traces?limit=500"),
  ]);
  const features = [...new Set(all.traces.map((t) => t.name))].sort();
  const href = (d: number, n?: string) => `/monitor?days=${d}${n ? `&name=${encodeURIComponent(n)}` : ""}`;
  const m = mon.measured;
  const drifted = drift.findings.filter((f) => f.drifted);

  return (
    <>
      <PageHead title="Production monitor" sub="What real traffic does. Every number says how we got it: counted, guessed by a judge, or not known at all." aside={<KindLegend />} />
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16, alignItems: "center" }}>
        <Segmented current={String(days)} items={[1, 7, 30].map((d) => ({ id: String(d), label: d === 1 ? "24 hours" : `${d} days`, href: href(d, sp.name) }))} />
        {features.length > 0 && <Segmented current={sp.name ?? "all"} items={[{ id: "all", label: "All features", href: href(days) }, ...features.map((f) => ({ id: f, label: f, href: href(days, f) }))]} />}
        <span className="hint mono">{mon.traces} traces</span>
      </div>

      {mon.traces === 0 ? <Panel><Empty title="No traces in this window">Send traces with the SDK, or widen the window. See the README: “Watch a system in production”.</Empty></Panel> : (
        <>
          <div className="grid-2">
            <Panel title="Measured" aside={<Kind kind="measured" />} delay={30}>
              <div style={{ display: "grid", gap: 18 }}>
                <Row name="Errors">{m.error_rate && <Rail rate={m.error_rate.value} ci={m.error_rate.ci} n={m.error_rate.n} bad={(m.error_rate.value ?? 0) > 0.04} />}</Row>
                <Row name="Answers that were empty">{m.empty_answer_rate && <Rail rate={m.empty_answer_rate.value} ci={m.empty_answer_rate.ci} n={m.empty_answer_rate.n} />}</Row>
                <Row name="Bad user feedback" note={m.user_feedback?.note}>{m.user_feedback && <Rail rate={m.user_feedback.bad.value} ci={m.user_feedback.bad.ci} n={m.user_feedback.bad.n} bad={(m.user_feedback.bad.value ?? 0) > 0.2} />}</Row>
                {Object.entries(m).filter(([k]) => k.startsWith("check:")).map(([k, v]) => {
                  const c = v as { pass_rate: { value: number; ci: [number, number]; n: number }; coverage: number };
                  return <Row key={k} name={`Rule check: ${k.slice(6)}`} note={`Run on ${pct(c.coverage, 0)} of traces`}><Rail rate={c.pass_rate.value} ci={c.pass_rate.ci} n={c.pass_rate.n} threshold={0.95} /></Row>;
                })}
                <dl className="kv" style={{ borderTop: "1px solid var(--rule)", paddingTop: 14 }}>
                  <dt>Latency P50 / P95 / P99</dt><dd>{ms(m.latency_ms?.p50)} / {ms(m.latency_ms?.p95)} / {ms(m.latency_ms?.p99)}</dd>
                  <dt>Cost</dt><dd>{m.cost_usd?.total == null ? "unknown" : `${usd(m.cost_usd.total, 2)} (${usd(m.cost_usd.per_trace, 5)} per trace)`}</dd>
                </dl>
              </div>
            </Panel>
            <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
              <Panel title="Estimated" aside={<Kind kind="estimated" />} delay={60}>
                {Object.keys(mon.estimated).length === 0 ? <p className="hint" style={{ margin: 0 }}>No judge-based check has results. Add one to the workspace policy to estimate groundedness or helpfulness on a sample.</p> : (
                  <div style={{ display: "grid", gap: 16 }}>
                    {Object.entries(mon.estimated).map(([k, v]) => (
                      <Row key={k} name={label(k.slice(6))} note={`A judge model’s opinion on ${pct(v.coverage, 0)} of traces. A judge can be wrong.`}>
                        <Rail rate={v.pass_rate.value} ci={v.pass_rate.ci} n={v.pass_rate.n} kind="estimated" threshold={0.85} />
                      </Row>
                    ))}
                  </div>
                )}
                {mon.online_checks && <p className="hint" style={{ margin: "14px 0 0" }}>{pct(mon.online_checks.sampled_share, 0)} of traces are sampled: {mon.online_checks.evaluated} checked, {mon.online_checks.pending} waiting, {mon.online_checks.failed_to_evaluate} failed.</p>}
              </Panel>
              <Panel title="Not measured" aside={<Kind kind="unmeasured" />} delay={90}>
                <ul style={{ margin: 0, paddingLeft: 18, listStyle: "disc", color: "var(--ink-2)", display: "grid", gap: 6 }}>{mon.unmeasured.map((u) => <li key={u}>{u}</li>)}</ul>
              </Panel>
            </div>
          </div>

          <div className="grid-2" style={{ marginTop: 16 }}>
            <Panel title="Traffic and errors by day" delay={110}>
              <div className="hint" style={{ marginBottom: 4 }}>Traces</div>
              <Sparkbars values={mon.daily.map((d) => d.traces)} labels={mon.daily.map((d) => d.date)} height={52} title="Traces per day" />
              <div className="hint" style={{ margin: "14px 0 4px" }}>Error rate</div>
              <Sparkbars values={mon.daily.map((d) => d.error_rate)} labels={mon.daily.map((d) => d.date)} format={(v) => pct(v)} height={52} highlight={0} title="Error rate per day" />
              <div className="hint mono" style={{ marginTop: 6, display: "flex", justifyContent: "space-between" }}><span>{mon.daily[0]?.date}</span><span>{mon.daily.at(-1)?.date}</span></div>
            </Panel>
            <Panel title="Has anything changed?" delay={130} aside={<span className="hint">last {days}d against the {Math.min(120, days * 4)}d before</span>} flush>
              {drift.warnings.length > 0 ? <Empty title="Not enough traces to compare">{drift.warnings[0]}</Empty> : drifted.length === 0 ? <Empty title="No measure changed">{drift.findings.length} measures were compared. None moved beyond what chance explains.</Empty> : (
                <>
                  {drifted.slice(0, 7).map((f) => (
                    <div key={f.feature} className="rowlink" style={{ alignItems: "center" }}>
                      <span className={`dot dot--${f.level === "large" ? "fail" : "warn"}`} style={{ marginTop: 0 }} />
                      <div style={{ flex: 1, minWidth: 0 }}><span style={{ fontWeight: 500 }}>{f.feature}</span><div className="hint mono" style={{ overflowWrap: "anywhere" }}>{f.reference} → {f.current}</div></div>
                      <span className="chip">{f.level}</span>
                    </div>
                  ))}
                  <p className="hint" style={{ margin: 0, padding: "10px 18px 14px" }}>{drift.note}</p>
                </>
              )}
            </Panel>
          </div>

          <Panel title="Traces that went wrong" id="flagged" delay={150} aside={<span className="hint">errors, bad ratings, failed checks</span>} flush>
            {(sp.error || sp.done) && <div style={{ padding: "14px 18px 0" }}><Notice error={sp.error} done={sp.done} /></div>}
            {flagged.length === 0 ? <Empty title="Nothing is flagged">No error, bad rating or failed check in these traces.</Empty> : flagged.map((f) => (
              <div key={f.trace_id} className="rowlink" style={{ alignItems: "center", flexWrap: "wrap" }}>
                <div style={{ flex: "1 1 260px", minWidth: 0 }}>
                  <span className="mono" style={{ fontWeight: 500 }}>{f.trace_id}</span> <span className="hint">{f.name}</span>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>{f.reasons.map((r) => <span key={r} className="chip">{label(r)}</span>)}</div>
                </div>
                {f.has_content ? (
                  <form action={promote} style={{ display: "flex", gap: 8 }}>
                    <input type="hidden" name="trace" value={f.trace_id} />
                    <input name="answer" className="input" style={{ minHeight: 30, width: 170, fontSize: 12 }} placeholder="Right answer (optional)" aria-label="Right answer" />
                    <button className="btn btn--sm">Make a test</button>
                  </form>
                ) : <span className="hint" style={{ maxWidth: 240 }}>No text is stored, so this cannot become a test.</span>}
              </div>
            ))}
          </Panel>

          <Panel title="Recent traces" delay={170} flush>
            <div className="scroll-x"><table className="tbl">
              <thead><tr><th>When</th><th>Feature</th><th>State</th><th>Model</th><th>Latency</th><th>Tokens</th><th>Cost</th><th>Rating</th></tr></thead>
              <tbody>{traces.map((t) => (
                <tr key={t.trace_id}>
                  <td className="mono">{ago(t.started_at)}</td><td>{t.name}</td>
                  <td><span className={`res res--${t.status === "ok" ? "pass" : "fail"}`}>{t.status}</span>{t.error_type && <span className="hint"> {t.error_type}</span>}</td>
                  <td className="mono">{t.model ?? "–"}</td><td className="mono">{ms(t.duration_ms)}</td>
                  <td className="mono">{(t.input_tokens ?? 0) + (t.output_tokens ?? 0) || "–"}</td><td className="mono">{usd(t.cost_usd, 5)}</td>
                  <td>{t.feedback == null ? <span className="hint">–</span> : t.feedback > 0 ? "good" : t.feedback < 0 ? <span style={{ color: "var(--fail)" }}>bad</span> : "–"}</td>
                </tr>
              ))}</tbody>
            </table></div>
          </Panel>
        </>
      )}
    </>
  );
}

function Row({ name, note, children }: { name: string; note?: string; children: React.ReactNode }) {
  return <div><div style={{ fontWeight: 500, marginBottom: 4 }}>{name}</div>{children}{note && <div className="hint" style={{ marginTop: 3 }}>{note}</div>}</div>;
}
