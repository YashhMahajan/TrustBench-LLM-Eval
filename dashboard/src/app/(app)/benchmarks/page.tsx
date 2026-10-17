import { api, apiOrNull } from "@/lib/api";
import { ago, label } from "@/lib/format";
import type { DatasetDetail, DatasetRow, Me, Proposal } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { Segmented } from "@/components/Controls";
import { Notice } from "@/components/Notice";
import { review } from "./actions";

export const metadata = { title: "Benchmarks" };
type SP = { v?: string; status?: string; error?: string; done?: string };

export default async function Benchmarks({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const status = ["proposed", "approved", "rejected"].includes(sp.status ?? "") ? sp.status! : "proposed";
  const [{ datasets }, { proposals }, me] = await Promise.all([
    api<{ datasets: DatasetRow[] }>("/v1/datasets"),
    api<{ proposals: Proposal[] }>(`/v1/proposals?status=${status}`),
    api<Me>("/v1/me"),
  ]);
  const chosen = sp.v ? Number(sp.v) : datasets[0]?.id;
  const detail = chosen ? await apiOrNull<DatasetDetail>(`/v1/datasets/${chosen}`) : null;
  const canReview = me.role !== "read";

  return (
    <>
      <PageHead title="Benchmarks" sub="The tests a model must pass. A dataset version never changes, so a result always points to the exact tests behind it." />
      <div className="grid-main">
        <Panel title="Dataset versions" delay={20} flush>
          {datasets.length === 0 ? <Empty title="No dataset yet">A dataset is stored when you run it: <code>trustbench run --dataset factual</code></Empty> : datasets.map((d) => {
            const total = Object.values(d.tasks).reduce((a, b) => a + b, 0);
            return (
              <a key={d.id} href={`/benchmarks?v=${d.id}`} className="rowlink" style={{ flexDirection: "column", gap: 8, background: d.id === chosen ? "var(--surface-2)" : undefined }}>
                <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap", width: "100%" }}>
                  <strong className="display" style={{ fontSize: 16 }}>{d.name}</strong><span className="mono hint">{d.version.slice(0, 10)}</span>
                  <span className="hint" style={{ marginLeft: "auto" }}>{d.cases} cases · {d.runs} run{d.runs === 1 ? "" : "s"} · {ago(d.created_at)}</span>
                </div>
                <div className="seg-bar" style={{ width: "100%" }} role="img" aria-label={Object.entries(d.tasks).map(([t, n]) => `${label(t)} ${n}`).join(", ")}>
                  {Object.entries(d.tasks).map(([t, n]) => <span key={t} style={{ width: `${(n / total) * 100}%` }} title={`${label(t)}: ${n}`} />)}
                </div>
                <div className="hint">{Object.entries(d.tasks).map(([t, n]) => `${label(t)} ${n}`).join(" · ")}</div>
              </a>
            );
          })}
        </Panel>

        <Panel title="Problems found in this version" delay={60} flush>
          {!detail ? <Empty title="Choose a version" /> : detail.issues.length === 0 ? <Empty title="No problems found">No duplicates, conflicting answers, missing references or personal data.</Empty> :
            detail.issues.slice(0, 10).map((i, k) => (
              <div key={k} className="rowlink"><span className={`dot dot--${i.severity === "error" ? "fail" : "warn"}`} />
                <span><span style={{ fontWeight: 500 }}>{label(i.code)}</span><span className="hint" style={{ display: "block" }}>{i.message} {i.case_ids.slice(0, 4).join(", ")}</span></span></div>
            ))}
        </Panel>
      </div>

      {detail && (
        <Panel title={`Cases in ${detail.name}`} delay={90} aside={<span className="hint">first {detail.preview.length} of {detail.cases}</span>} flush>
          <div className="scroll-x" style={{ maxHeight: 340, overflowY: "auto" }}><table className="tbl">
            <thead><tr><th>Case</th><th>Task</th><th>Question</th><th>Checked by</th></tr></thead>
            <tbody>{detail.preview.map((c) => (
              <tr key={c.id}><td className="mono">{c.id}</td><td>{label(c.task)}</td><td style={{ maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.question}</td>
                <td><span style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>{c.evaluators.map((e) => <span key={e} className="chip">{label(e)}</span>)}</span></td></tr>
            ))}</tbody>
          </table></div>
        </Panel>
      )}

      <Panel title="Proposed tests" id="proposals" delay={120} aside={<Segmented current={status} items={["proposed", "approved", "rejected"].map((s) => ({ id: s, label: s, href: `/benchmarks?status=${s}${sp.v ? `&v=${sp.v}` : ""}#proposals` }))} />} flush>
        {(sp.error || sp.done) && <div style={{ padding: "14px 18px 0" }}><Notice error={sp.error} done={sp.done} /></div>}
        {proposals.length === 0 ? <Empty title={`No ${status} proposals`}>{status === "proposed" ? <>Proposals come from failed cases: <code>trustbench proposals generate RUN_ID</code>, or from production: Monitor → Make a test.</> : "Nothing here yet."}</Empty> :
          proposals.slice(0, 25).map((p) => (
            <div key={p.id} className="rowlink" style={{ flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ flex: "1 1 340px", minWidth: 0 }}>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
                  <span className="mono" style={{ fontWeight: 500 }}>{p.case_id}</span>
                  <span className="chip">{p.method.replace("transform:", "changed copy: ")}</span>
                  {p.answer_checked ? <span className="chip">answer checked</span> : <span className="chip" style={{ borderColor: "var(--warn)", color: "var(--warn)" }}>answer not checked</span>}
                </div>
                <div className="hint" style={{ overflowWrap: "anywhere", marginTop: 3 }}>{Object.values(p.spec.input).join(" — ").slice(0, 200)}</div>
                <div className="hint mono">{p.spec.evaluation.evaluators.map((e) => e.type).join(", ")} → {JSON.stringify(p.spec.expected).slice(0, 80)}</div>
              </div>
              {p.status === "proposed" && canReview && (
                <form action={review} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <input type="hidden" name="id" value={p.id} />
                  {!p.answer_checked && <label className="check hint"><input type="checkbox" name="checked" />I checked it</label>}
                  <button className="btn btn--sm btn--primary" name="decision" value="approve">Approve</button>
                  <button className="btn btn--sm" name="decision" value="reject">Reject</button>
                </form>
              )}
            </div>
          ))}
      </Panel>
    </>
  );
}
