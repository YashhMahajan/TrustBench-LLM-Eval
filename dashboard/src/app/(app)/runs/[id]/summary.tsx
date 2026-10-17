import Link from "next/link";
import { api } from "@/lib/api";
import { label, ms, pct, usd } from "@/lib/format";
import type { RunDetail, SliceRow } from "@/lib/types";
import { Empty, Panel } from "@/components/Panel";
import { Kind } from "@/components/Stamp";
import { Rail } from "@/components/Rail";

export async function Summary({ run, model, id }: { run: RunDetail; model: string; id: string }) {
  const m = run.models[model];
  const ops = m.operations;
  const { slices } = await api<{ slices: SliceRow[] }>(`/v1/runs/${id}/slices?model=${encodeURIComponent(model)}`);
  const tasks = Object.entries(m.by_task).sort((a, b) => (a[1].rate ?? 0) - (b[1].rate ?? 0));
  const weak = slices.filter((s) => s.weak);
  const h = m.hallucination;

  return (
    <div className="grid-main">
      <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
        <Panel title="Pass rate" aside={<Kind kind="measured" />} delay={30}>
          <div style={{ marginBottom: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><strong className="display" style={{ fontSize: 17 }}>All cases</strong><span className="hint mono">{model}</span></div>
            <Rail rate={m.overall.rate} ci={m.overall.ci} n={m.overall.n} />
          </div>
          <div style={{ borderTop: "1px solid var(--rule)", paddingTop: 14, display: "grid", gap: 14 }}>
            {tasks.map(([t, s], i) => (
              <div key={t} style={{ display: "grid", gridTemplateColumns: "minmax(110px, 170px) 1fr", gap: 14, alignItems: "center" }}>
                <span style={{ fontWeight: 500 }}>{label(t)}</span>
                <Rail rate={s.rate} ci={s.ci} n={s.n} bad={weak.some((w) => w.dimension === "task" && w.value === t)} delay={i * 30} />
              </div>
            ))}
          </div>
          <p className="hint" style={{ margin: "16px 0 0" }}>The bar is the 95% interval: where the true pass rate probably is. Few cases make a long bar. Red marks an area that is clearly weaker than the rest.</p>
          {(m.left_out.skipped_cases > 0 || m.left_out.evaluator_errors > 0) && (
            <div className="callout callout--warn" style={{ marginTop: 14 }}>Left out of these rates: {m.left_out.skipped_cases} skipped case(s) and {m.left_out.evaluator_errors} result(s) where an evaluator broke.</div>
          )}
        </Panel>

        <Panel title="Weak areas" delay={70} aside={<span className="hint">tested against all other cases</span>} flush>
          {weak.length === 0 ? <Empty title="No area is clearly weaker">With false discovery control, no group of cases stands out from the rest.</Empty> :
            weak.map((s) => (
              <div key={s.dimension + s.value} className="rowlink" style={{ alignItems: "center" }}>
                <span className="dot dot--fail" style={{ marginTop: 0 }} />
                <div style={{ flex: 1 }}><span style={{ fontWeight: 500 }}>{label(s.dimension)} = {label(s.value)}</span><div className="hint">p = {s.p_value?.toFixed(4)}</div></div>
                <div style={{ width: "min(46%, 260px)" }}><Rail rate={s.rate} ci={s.ci} n={s.n} bad compact /></div>
                <div className="mono hint">vs {pct(s.rest_rate, 0)}</div>
              </div>
            ))}
        </Panel>
      </div>

      <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
        <Panel title="Speed, errors and cost" delay={50}>
          <dl className="kv">
            <dt>Latency P50</dt><dd>{ms(ops.latency_ms["50"])}</dd>
            <dt>Latency P95</dt><dd>{ms(ops.latency_ms["95"])}</dd>
            <dt>Latency P99</dt><dd>{ms(ops.latency_ms["99"])}</dd>
            <dt>Calls that failed</dt><dd>{pct(ops.call_failure_rate)}</dd>
            <dt>Calls retried</dt><dd>{pct(ops.retry_rate)}</dd>
            <dt>Cost of the run</dt><dd>{usd(ops.cost_usd, 3)}</dd>
            <dt>Cost per success</dt><dd>{usd(ops.cost_per_success_usd)}</dd>
          </dl>
          {ops.priced_share != null && ops.priced_share < 1 && <p className="hint" style={{ margin: "12px 0 0" }}>Only {pct(ops.priced_share, 0)} of calls had a price, so the cost is too low.</p>}
        </Panel>
        {h && h.total ? (
          <Panel title="Claims" aside={<Kind kind="estimated" />} delay={90}>
            <dl className="kv">
              <dt>Claims checked</dt><dd>{h.total}</dd>
              <dt>Supported by evidence</dt><dd>{pct(h.ec as number)}</dd>
              <dt>Not supported</dt><dd>{pct(h.ucr as number)}</dd>
              <dt>Contradicted</dt><dd>{pct(h.cr as number)}</dd>
              <dt>Could not be judged</dt><dd>{pct(h.unassessed_rate as number)}</dd>
            </dl>
          </Panel>
        ) : null}
        {m.needs_human_review > 0 && (
          <div className="callout callout--warn">{m.needs_human_review} result{m.needs_human_review > 1 ? "s" : ""} need a person to check them. <Link href={`/runs/${id}?tab=cases`} style={{ textDecoration: "underline" }}>Open the cases</Link></div>
        )}
        <Panel title="Setup" delay={110}>
          <dl className="kv">
            <dt>Models</dt><dd>{run.config.models.join(", ")}</dd>
            <dt>Repetitions</dt><dd>{String(run.config.options.repetitions ?? 1)}</dd>
            <dt>Software</dt><dd>trustbench {run.software_version}</dd>
          </dl>
        </Panel>
      </div>
    </div>
  );
}
