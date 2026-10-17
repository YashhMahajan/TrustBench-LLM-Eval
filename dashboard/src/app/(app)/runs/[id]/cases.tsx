import Link from "next/link";
import { api, apiOrNull } from "@/lib/api";
import { label, ms, pct } from "@/lib/format";
import type { CaseDetail, CaseRow } from "@/lib/types";
import { Empty, Panel } from "@/components/Panel";
import { EscClose } from "@/components/EscClose";
import type { SP } from "./page";

export async function Cases({ id, model, models, sp, q }: { id: string; model: string; models: string[]; sp: SP; q: (e: Record<string, string | undefined>) => string }) {
  const params = new URLSearchParams({ model, limit: "150" });
  (["result", "task", "q"] as const).forEach((k) => sp[k] && params.set(k, sp[k]!));
  const data = await api<{ total: number; cases: CaseRow[]; tasks: string[] }>(`/v1/runs/${id}/cases?${params}`);
  const withCase = (c?: string) => q({ tab: "cases", case: c, result: sp.result, task: sp.task, q: sp.q });
  const open = sp.case ? await apiOrNull<CaseDetail>(`/v1/runs/${id}/cases/${encodeURIComponent(sp.case)}?model=${encodeURIComponent(model)}`) : null;

  return (
    <>
      <Panel flush>
        <form className="filters" method="get">
          <input type="hidden" name="tab" value="cases" />
          {models.length > 1 && <input type="hidden" name="model" value={model} />}
          <select name="result" className="select" defaultValue={sp.result ?? ""} aria-label="Result">
            <option value="">Any result</option><option value="fail">Failed</option><option value="pass">Passed</option><option value="error">Errors</option>
          </select>
          <select name="task" className="select" defaultValue={sp.task ?? ""} aria-label="Task">
            <option value="">Any task</option>{data.tasks.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </select>
          <input name="q" className="input" style={{ maxWidth: 260, minHeight: 34 }} placeholder="Search id, question or answer" defaultValue={sp.q ?? ""} aria-label="Search" />
          <button className="btn btn--sm btn--primary">Filter</button>
          {(sp.result || sp.task || sp.q) && <Link href={q({ tab: "cases" })} className="btn btn--sm">Clear</Link>}
          <span className="hint" style={{ marginLeft: "auto" }}>{data.total} case{data.total === 1 ? "" : "s"}{data.total > data.cases.length && `, first ${data.cases.length} shown`}</span>
        </form>
        {data.cases.length === 0 ? <Empty title="No case matches">Change or clear the filters.</Empty> : (
          <div className="scroll-x">
            <table className="tbl tbl--click">
              <thead><tr><th>Result</th><th>Case</th><th>Task</th><th>What went wrong</th><th>Answer</th><th>Latency</th></tr></thead>
              <tbody>
                {data.cases.map((c) => (
                  <tr key={c.case_id + c.repetition} aria-selected={sp.case === c.case_id}>
                    <td><span className={`res res--${c.result}`}>{c.result}</span></td>
                    <td className="mono"><Link href={withCase(c.case_id)} scroll={false} style={{ fontWeight: 500, textDecoration: "underline", textDecorationColor: "var(--rule-strong)", textUnderlineOffset: 3 }}>{c.case_id}</Link></td>
                    <td>{label(c.task)}</td>
                    <td>{c.labels.length ? <span style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>{c.labels.map((l) => <span key={l} className="chip">{label(l)}</span>)}</span> : <span className="hint">–</span>}</td>
                    <td className="hint" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.output || "–"}</td>
                    <td className="mono">{ms(c.latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {open && <Drawer detail={open} close={withCase(undefined)} />}
    </>
  );
}

function Drawer({ detail, close }: { detail: CaseDetail; close: string }) {
  return (
    <>
      <Link href={close} className="drawer__scrim" aria-label="Close" scroll={false} />
      <aside className="drawer" role="dialog" aria-label={`Case ${detail.case_id}`}>
        <EscClose href={close} />
        <div className="drawer__head">
          <div><div className="mono" style={{ fontWeight: 600, fontSize: 15 }}>{detail.case_id}</div><div className="hint">{label(detail.task)}</div></div>
          <Link href={close} className="btn btn--sm" scroll={false}>Close</Link>
        </div>
        <div className="drawer__body">
          {detail.executions.map((e, i) => (
            <div key={i} style={{ display: "grid", gap: 14 }}>
              {detail.executions.length > 1 && <div className="mono hint">repetition {e.repetition}</div>}
              <div><div className="field__label">Question</div><div className="field__box">{Object.entries(e.input).map(([k, v]) => (k === "prompt" ? v : `${k}: ${v}`)).join("\n\n")}</div></div>
              {Object.keys(e.expected).length > 0 && <div><div className="field__label">Expected</div><div className="field__box">{JSON.stringify(e.expected, null, 2)}</div></div>}
              <div><div className="field__label">Answer</div><div className="field__box">{e.output ?? (e.error ? `No answer. ${e.error}` : "No answer.")}</div></div>
              {e.attempts.filter((a) => !a.ok).length > 0 && <div className="callout callout--warn">{e.attempts.filter((a) => !a.ok).length} call attempt(s) failed: {e.attempts.filter((a) => !a.ok)[0].error}</div>}
              {e.labels.length > 0 && <div><div className="field__label">What went wrong</div><span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{e.labels.map((l) => <span key={l} className="chip">{label(l)}</span>)}</span></div>}
              <div>
                <div className="field__label">Checks</div>
                <div style={{ display: "grid", gap: 8 }}>
                  {e.results.map((r) => (
                    <div key={r.evaluator} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                      <span className={`res res--${r.passed ? "pass" : "fail"}`}>{r.passed ? "pass" : "fail"}</span>
                      <div style={{ minWidth: 0 }}><div className="mono" style={{ fontWeight: 500 }}>{r.evaluator}</div><div className="hint" style={{ overflowWrap: "anywhere" }}>{r.rationale}</div>
                        {r.details.human_review ? <div style={{ color: "var(--warn)", fontSize: 12.5 }}>A person should check this result.</div> : null}</div>
                    </div>
                  ))}
                  {e.results.length === 0 && <span className="hint">No check ran, because the call failed.</span>}
                </div>
              </div>
              {e.rag.stage && <div className="callout"><strong>Stage that failed: {label(e.rag.stage)}.</strong> {e.rag.explanation}</div>}
            </div>
          ))}
          {Object.keys(detail.metadata).length > 0 && <div><div className="field__label">Metadata</div><div className="field__box">{JSON.stringify(detail.metadata, null, 2)}</div></div>}
        </div>
      </aside>
    </>
  );
}
