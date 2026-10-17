import Link from "next/link";
import { api } from "@/lib/api";
import { ago, label, shortId, usd } from "@/lib/format";
import type { RunRow } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { KindLegend, Stamp } from "@/components/Stamp";
import { Rail } from "@/components/Rail";

export const metadata = { title: "Experiments" };

export default async function Runs() {
  const { runs } = await api<{ runs: RunRow[] }>("/v1/runs?limit=50");
  return (
    <>
      <PageHead title="Experiments" sub="Every run, with the pass rate of each model and how sure we are of it. A short bar means a sure result." aside={<KindLegend />} />
      <Panel flush>
        {runs.length === 0 ? <Empty title="No runs yet">Run a dataset against your models: <code>trustbench run</code>. Or load sample data: <code>trustbench demo</code>.</Empty> : (
          <div className="scroll-x">
            <table className="tbl tbl--click">
              <thead><tr><th>Run</th><th>Dataset</th><th>Model</th><th style={{ width: "36%" }}>Pass rate</th><th>Cost</th><th>State</th></tr></thead>
              <tbody>
                {runs.flatMap((r, i) => Object.entries(r.models).map(([m, v], j, all) => (
                  <tr key={r.id + m} style={j < all.length - 1 ? { borderBottomColor: "transparent" } : undefined}>
                    {j === 0 && (
                      <td rowSpan={all.length} style={{ verticalAlign: "top" }}>
                        <Link href={`/runs/${r.id}`} style={{ fontWeight: 600 }}>{r.name ?? shortId(r.id)}</Link>
                        <div className="hint mono">{shortId(r.id)} · {ago(r.created_at)}</div>
                        {r.baseline && <span className="chip" style={{ marginTop: 6 }}>baseline “{r.baseline}”</span>}
                      </td>
                    )}
                    {j === 0 && <td rowSpan={all.length} style={{ verticalAlign: "top" }}><span className="mono">{r.dataset}</span><div className="hint">{r.cases} cases</div></td>}
                    <td className="mono">{m}</td>
                    <td><Rail rate={v.rate} ci={v.ci} n={v.n} delay={i * 30 + j * 50} /></td>
                    {j === 0 && <td rowSpan={all.length} className="mono" style={{ verticalAlign: "top" }}>{usd(r.cost_usd, 3)}{r.judge_cost_usd > 0 && <div className="hint">judge {usd(r.judge_cost_usd, 3)}</div>}</td>}
                    {j === 0 && <td rowSpan={all.length} style={{ verticalAlign: "top" }}>{r.status === "complete" ? <span className="chip">{label(r.status)}</span> : <Stamp status="incomplete" />}</td>}
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
