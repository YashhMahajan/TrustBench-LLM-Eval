import Link from "next/link";
import { api } from "@/lib/api";
import { label, pp, shortId } from "@/lib/format";
import type { ExperimentRow, ExperimentVerdict } from "@/lib/types";
import { Empty, PageHead, Panel } from "@/components/Panel";
import { Rail } from "@/components/Rail";

export const metadata = { title: "Variant history" };

// What the evidence says about the idea behind a change. "Mixed" is the important one: better overall, but a slice got worse.
const VERDICT: Record<ExperimentVerdict, { text: string; tone: "pass" | "fail" | "warn" | "neutral"; why: string }> = {
  supported: { text: "Supported", tone: "pass", why: "Better, and no slice got clearly worse." },
  mixed: { text: "Mixed", tone: "warn", why: "Better overall, but a slice got clearly worse." },
  refuted: { text: "Refuted", tone: "fail", why: "Worse than the variant it changed." },
  no_effect: { text: "No real effect", tone: "neutral", why: "The change is smaller than the threshold that matters." },
  cannot_tell: { text: "Cannot tell", tone: "warn", why: "Not enough cases to say." },
};

export default async function Experiments() {
  const { experiments } = await api<{ experiments: ExperimentRow[] }>("/v1/experiments");
  return (
    <>
      <PageHead
        title="Variant history"
        sub="What we tried, why, and what the evidence said. Each variant is compared with the one it changed. Record one with trustbench experiment record."
      />
      {experiments.length === 0 ? (
        <Panel>
          <Empty title="No variants yet">
            Name a run as a variant, with your hypothesis: <code>trustbench experiment record v1 RUN_ID --parent v0 --hypothesis &quot;...&quot;</code>
          </Empty>
        </Panel>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          {experiments.map((e, i) => {
            const v = e.verdict ? VERDICT[e.verdict] : null;
            return (
              <Panel key={e.name} delay={i * 40} title={e.name} aside={e.parent ? <span className="hint">changes {e.parent}</span> : <span className="hint">baseline</span>}>
                <div style={{ display: "grid", gap: 12 }}>
                  {e.hypothesis && <p style={{ margin: 0 }}><strong>Hypothesis.</strong> {e.hypothesis}</p>}
                  <Rail rate={e.rate} ci={e.ci} n={e.n} delay={i * 40} />
                  {v && (
                    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                      <span className={`res res--${v.tone}`}>{v.text}</span>
                      <span className="num">{pp(e.change)}{e.ci_change ? ` (interval ${pp(e.ci_change[0])} to ${pp(e.ci_change[1])})` : ""}</span>
                      <span className="hint">{v.why}</span>
                    </div>
                  )}
                  {e.regressed_slices.length > 0 && (
                    <div className="callout callout--warn">
                      A slice got clearly worse: {e.regressed_slices.map((s) => <span key={s} className="chip" style={{ marginLeft: 6 }}>{label(s)}</span>)}
                    </div>
                  )}
                  <div className="hint mono">
                    run <Link href={`/runs/${e.run_id}`}>{shortId(e.run_id)}</Link>{e.model ? ` · ${e.model}` : ""}
                  </div>
                </div>
              </Panel>
            );
          })}
        </div>
      )}
    </>
  );
}
