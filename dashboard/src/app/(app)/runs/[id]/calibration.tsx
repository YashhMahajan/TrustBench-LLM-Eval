import { api } from "@/lib/api";
import { pct } from "@/lib/format";
import type { Calibration } from "@/lib/types";
import { Panel } from "@/components/Panel";

export async function CalibrationTab({ id, model }: { id: string; model: string }) {
  const c = await api<Calibration>(`/v1/runs/${id}/calibration?model=${encodeURIComponent(model)}`);
  const S = 280, pad = 34;
  const x = (v: number) => pad + v * (S - pad - 10);
  const y = (v: number) => S - pad - v * (S - pad - 10);
  return (
    <div className="grid-main">
      <Panel title="Does the model know when it is right?" delay={20}>
        <svg viewBox={`0 0 ${S} ${S}`} width="100%" style={{ maxWidth: 420 }} role="img" aria-label="Reliability diagram: stated confidence against actual accuracy">
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}><line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(1)} stroke="var(--rule)" /><line x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} stroke="var(--rule)" />
              <text x={x(t)} y={S - 16} fontSize="9" textAnchor="middle" fill="var(--muted)" className="mono">{t * 100}</text>
              <text x={x(0) - 6} y={y(t) + 3} fontSize="9" textAnchor="end" fill="var(--muted)" className="mono">{t * 100}</text></g>
          ))}
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--ink-2)" strokeDasharray="4 4" />
          {c.bins.map((b) => (
            <g key={b.low}>
              <circle cx={x(b.mean_confidence)} cy={y(b.accuracy)} r={4 + Math.sqrt(b.n) * 1.6} fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="1.5">
                <title>{`Said ${pct(b.mean_confidence, 0)} sure, was right ${pct(b.accuracy, 0)} of the time (${b.n} answers)`}</title>
              </circle>
            </g>
          ))}
          <text x={S / 2} y={S - 3} fontSize="10" textAnchor="middle" fill="var(--muted)">stated confidence (%)</text>
          <text transform={`translate(9 ${S / 2}) rotate(-90)`} fontSize="10" textAnchor="middle" fill="var(--muted)">actually right (%)</text>
        </svg>
        <p className="hint" style={{ margin: "8px 0 0" }}>A point on the dashed line is honest. A point below it is overconfident: it said “90% sure” but was right less often. Bigger points hold more answers.</p>
      </Panel>
      <Panel title="Numbers" delay={60}>
        <dl className="kv">
          <dt>Answers with a confidence</dt><dd>{c.n}</dd>
          <dt>Accuracy</dt><dd>{pct(c.accuracy, 0)}</dd>
          <dt>Average stated confidence</dt><dd>{pct(c.mean_confidence, 0)}</dd>
          <dt>Brier score (lower is better)</dt><dd>{c.brier?.toFixed(3) ?? "–"}</dd>
          <dt>Calibration error</dt><dd>{c.ece?.toFixed(3) ?? "–"}</dd>
          <dt>Can confidence tell right from wrong? (AUROC)</dt><dd>{c.auroc?.toFixed(2) ?? "–"}</dd>
          <dt>Error rate if it answers its top 50%</dt><dd>{pct(c.risk_at_50, 0)}</dd>
          <dt>Error rate if it answers its top 80%</dt><dd>{pct(c.risk_at_80, 0)}</dd>
        </dl>
        {c.warnings.map((w) => <p key={w} className="hint" style={{ margin: "10px 0 0" }}>{w}</p>)}
      </Panel>
    </div>
  );
}
