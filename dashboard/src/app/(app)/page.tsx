import Link from "next/link";
import { api, apiOrNull } from "@/lib/api";
import { STATUS_HEADLINE, ago, label, ms, pct, shortId } from "@/lib/format";
import type { Decision, Drift, Monitor, Proposal, RunDetail, RunRow } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { Kind, KindLegend, Stamp } from "@/components/Stamp";
import { Rail } from "@/components/Rail";
import { Sparkbars } from "@/components/Sparkbars";

export const metadata = { title: "Overview" };

interface Item { tone: "fail" | "warn" | "info"; text: string; href: string; detail?: string }

export default async function Overview() {
  const [{ runs }, { decisions }, monitor, drift, { proposals }] = await Promise.all([
    api<{ runs: RunRow[] }>("/v1/runs?limit=8"),
    api<{ decisions: Decision[] }>("/v1/decisions?limit=1"),
    api<Monitor>("/v1/monitor?days=7"),
    api<Drift>("/v1/drift?current_days=5&reference_days=30"),
    api<{ proposals: Proposal[] }>("/v1/proposals?status=proposed"),
  ]);
  const latest = decisions[0];
  const latestRun = runs[0] ? await apiOrNull<RunDetail>(`/v1/runs/${runs[0].id}`) : null;
  const weak = latestRun ? Object.entries(latestRun.models).flatMap(([model, m]) => m.weak_slices.slice(0, 2).map((s) => ({ model, ...s }))) : [];
  const drifted = drift.findings.filter((f) => f.drifted);
  const open = latest ? latest.checks.filter((c) => c.status !== "pass") : [];

  const attention: Item[] = [];
  if (latest && latest.status !== "pass") attention.push({ tone: latest.status === "fail" ? "fail" : "warn", text: `${runLabel(runs, latest.run_id)} is ${latest.status === "fail" ? "blocked" : latest.status}`, detail: `${open.length} of ${latest.checks.length} rules ${latest.status === "fail" ? "need work" : "lack evidence"}`, href: "/regression" });
  weak.slice(0, 2).forEach((w) => attention.push({ tone: "warn", text: `Weak area: ${label(w.dimension)} = ${label(w.value)}`, detail: `${pct(w.rate, 0)} pass, against ${pct(w.rest_rate, 0)} on the other cases (n=${w.n})`, href: `/runs/${latestRun?.run_id}` }));
  if (drifted.length) attention.push({ tone: "warn", text: `${drifted.length} production measure${drifted.length > 1 ? "s" : ""} changed`, detail: drifted.slice(0, 3).map((d) => d.feature).join(", "), href: "/monitor" });
  if (proposals.length) attention.push({ tone: "info", text: `${proposals.length} proposed test${proposals.length > 1 ? "s" : ""} wait for review`, detail: "From failed cases. A person must approve them.", href: "/benchmarks#proposals" });
  runs.filter((r) => r.status !== "complete").slice(0, 2).forEach((r) => attention.push({ tone: "warn", text: `Run ${r.name ?? shortId(r.id)} is ${r.status}`, detail: "Some cases did not finish, so its rates leave them out.", href: `/runs/${r.id}` }));

  const err = monitor.measured.error_rate;
  const lat = monitor.measured.latency_ms;
  const fb = monitor.measured.user_feedback;

  return (
    <>
      <PageHead title="Overview" sub="What the evidence says about this workspace right now." aside={<KindLegend />} />

      <div className="grid-main">
        <Panel title="Release readiness" delay={40} aside={latest && <Stamp status={latest.status} large />}>
          {latest ? (
            <>
              <h2 style={{ fontSize: "clamp(1.4rem, 2.6vw, 1.9rem)", lineHeight: 1.1 }}>{STATUS_HEADLINE[latest.status]}</h2>
              <p className="hint" style={{ margin: "6px 0 16px" }}>
                <Link href={`/runs/${latest.run_id}`} className="mono" style={{ color: "var(--accent-strong)" }}>{runLabel(runs, latest.run_id)}</Link>{" "}
                ({latest.model}) against policy “{latest.policy}” · {ago(latest.at)}
              </p>
              {open.length === 0 ? (
                <p style={{ margin: 0 }}>All {latest.checks.length} rules are met with enough evidence.</p>
              ) : (
                <div style={{ border: "1px solid var(--rule)", borderRadius: 8 }}>
                  {open.slice(0, 5).map((c) => (
                    <div key={c.rule} className="rowlink" style={{ alignItems: "center" }}>
                      <Stamp status={c.status} />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="mono" style={{ fontWeight: 500 }}>{c.rule.replace(/_/g, " ")}</div>
                        <div className="hint" style={{ overflowWrap: "anywhere" }}>needs {c.required}</div>
                      </div>
                      <div className="mono hint" style={{ maxWidth: "46%", textAlign: "right", overflowWrap: "anywhere" }}>{c.observed}</div>
                    </div>
                  ))}
                </div>
              )}
              <div style={{ marginTop: 14 }}><Link href="/regression" className="btn btn--sm">See every rule</Link></div>
            </>
          ) : (
            <Empty title="No release decision yet">Set a baseline and check a run: <code>trustbench gate check RUN_ID</code></Empty>
          )}
        </Panel>

        <Panel title="Needs attention" delay={80} flush>
          {attention.length === 0 ? <Empty title="Nothing needs you">Runs finished, no rule broken, no change in production.</Empty> :
            attention.map((a, i) => (
              <Link key={i} href={a.href} className="rowlink">
                <span className={`dot dot--${a.tone}`} />
                <span><span style={{ fontWeight: 500 }}>{a.text}</span>{a.detail && <span className="hint" style={{ display: "block" }}>{a.detail}</span>}</span>
              </Link>
            ))}
        </Panel>
      </div>

      <div className="grid-main" style={{ marginTop: 16 }}>
        <Panel title="Latest runs" delay={120} aside={<Link href="/runs" className="hint">All runs →</Link>} flush>
          {runs.length === 0 ? <Empty title="No runs yet">Run your first evaluation: <code>trustbench run</code>, or load sample data with <code>trustbench demo</code>.</Empty> : (
            <div>
              {runs.slice(0, 5).map((r, i) => (
                <Link key={r.id} href={`/runs/${r.id}`} className="rowlink" style={{ flexDirection: "column", gap: 8 }}>
                  <div style={{ display: "flex", width: "100%", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600 }}>{r.name ?? shortId(r.id)}</span>
                    <span className="hint mono">{r.dataset} · {r.cases} cases · {ago(r.created_at)}</span>
                    {r.baseline && <span className="chip">baseline “{r.baseline}”</span>}
                    {r.status !== "complete" && <Stamp status="incomplete" />}
                  </div>
                  <div style={{ width: "100%", display: "grid", gap: 6 }}>
                    {Object.entries(r.models).map(([m, v], j) => (
                      <div key={m} style={{ display: "grid", gridTemplateColumns: "minmax(88px, 130px) 1fr", gap: 12, alignItems: "center" }}>
                        <span className="mono hint" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{m.replace("demo:", "")}</span>
                        <Rail rate={v.rate} ci={v.ci} n={v.n} delay={i * 40 + j * 60} />
                      </div>
                    ))}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Production, last 7 days" delay={160} aside={<Link href="/monitor" className="hint">Monitor →</Link>}>
          {monitor.traces === 0 ? <Empty title="No traces yet">Send traces with the SDK. See the README, “Watch a system in production”.</Empty> : (
            <div style={{ display: "grid", gap: 16 }}>
              <div>
                <div className="hint" style={{ marginBottom: 4, display: "flex", justifyContent: "space-between" }}><span>Errors</span><Kind kind="measured" /></div>
                {err && <Rail rate={err.value} ci={err.ci} n={err.n} bad={(err.value ?? 0) > 0.04} />}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <div><div className="hint">Latency P95</div><div className="num" style={{ font: "600 20px var(--font-mono)" }}>{ms(lat?.p95)}</div></div>
                <div><div className="hint">Bad feedback</div><div className="num" style={{ font: "600 20px var(--font-mono)" }}>{pct(fb?.bad.value, 0)}</div><div className="hint">{pct(fb?.rated_share, 0)} of traces rated</div></div>
              </div>
              <div>
                <div className="hint" style={{ marginBottom: 6 }}>Traces per day</div>
                <Sparkbars values={monitor.daily.map((d) => d.traces)} labels={monitor.daily.map((d) => d.date)} highlight={Math.min(5, monitor.daily.length)} title="Traces per day" />
              </div>
              <div className="hint">
                {Object.keys(monitor.estimated).length > 0 && <>Judge opinions on a sample are <Kind kind="estimated" />. </>}
                {monitor.unmeasured.length} quality measure{monitor.unmeasured.length === 1 ? "" : "s"} <Kind kind="unmeasured" /> in production.
              </div>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

function runLabel(runs: RunRow[], id: string) {
  const r = runs.find((x) => x.id === id);
  return r?.name ?? shortId(id);
}
