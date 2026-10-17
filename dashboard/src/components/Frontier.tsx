import { pct } from "@/lib/format";
import type { Candidate } from "@/lib/types";

/** Quality (up) against cost per success (right, log scale). Models on the frontier are filled. */
export function Frontier({ items }: { items: Candidate[] }) {
  const pts = items.filter((c) => c.quality != null && c.cost_per_success != null && c.cost_per_success > 0);
  if (pts.length === 0) return null;
  const W = 640, H = 300, L = 54, R = 20, T = 16, B = 44;
  const costs = pts.map((c) => c.cost_per_success!);
  const lo = Math.log10(Math.min(...costs)) - 0.25, hi = Math.log10(Math.max(...costs)) + 0.25;
  const qs = pts.flatMap((c) => [c.quality!, c.quality_ci?.[0] ?? c.quality!, c.quality_ci?.[1] ?? c.quality!]);
  const qlo = Math.max(0, Math.floor((Math.min(...qs) - 0.05) * 10) / 10), qhi = Math.min(1, Math.ceil((Math.max(...qs) + 0.02) * 10) / 10);
  const x = (c: number) => L + ((Math.log10(c) - lo) / (hi - lo)) * (W - L - R);
  const y = (q: number) => T + (1 - (q - qlo) / (qhi - qlo)) * (H - T - B);
  const front = pts.filter((c) => c.on_frontier).sort((a, b) => a.cost_per_success! - b.cost_per_success!);
  const ticks = Array.from({ length: Math.ceil(hi) - Math.floor(lo) + 1 }, (_, i) => Math.floor(lo) + i).flatMap((t) => [1, 2, 5].map((m) => m * 10 ** t)).filter((v) => Math.log10(v) >= lo && Math.log10(v) <= hi);
  const yt = Array.from({ length: Math.round((qhi - qlo) * 10) + 1 }, (_, i) => qlo + i / 10).filter((_, i) => i % (qhi - qlo > 0.5 ? 2 : 1) === 0);
  const money = (c: number) => (c >= 0.01 ? `$${c.toFixed(2)}` : `$${c.toPrecision(2)}`);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Quality against cost per success. The frontier joins the models that no other model beats.">
      {yt.map((q) => <g key={q}><line x1={L} x2={W - R} y1={y(q)} y2={y(q)} stroke="var(--rule)" /><text x={L - 8} y={y(q) + 4} fontSize="11" textAnchor="end" fill="var(--muted)" className="mono">{Math.round(q * 100)}%</text></g>)}
      {ticks.map((v) => <g key={v}><line x1={x(v)} x2={x(v)} y1={T} y2={H - B} stroke="var(--rule)" /><text x={x(v)} y={H - B + 18} fontSize="11" textAnchor="middle" fill="var(--muted)" className="mono">{money(v)}</text></g>)}
      <text x={(L + W - R) / 2} y={H - 6} fontSize="11" textAnchor="middle" fill="var(--muted)">cost per successful answer (log scale), cheaper to the left</text>
      {front.length > 1 && <polyline fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeDasharray="5 4" points={front.map((c) => `${x(c.cost_per_success!)},${y(c.quality!)}`).join(" ")} />}
      {pts.map((c) => {
        const cx = x(c.cost_per_success!), cy = y(c.quality!);
        return (
          <g key={c.name} opacity={c.feasible === false ? 0.35 : 1}>
            {c.quality_ci && <line x1={cx} x2={cx} y1={y(c.quality_ci[0])} y2={y(c.quality_ci[1])} stroke="var(--ink-2)" strokeWidth="1.5" />}
            {c.quality_ci && [0, 1].map((i) => <line key={i} x1={cx - 4} x2={cx + 4} y1={y(c.quality_ci![i])} y2={y(c.quality_ci![i])} stroke="var(--ink-2)" strokeWidth="1.5" />)}
            <circle cx={cx} cy={cy} r={7} fill={c.on_frontier ? "var(--accent)" : "var(--surface)"} stroke={c.on_frontier ? "var(--accent-strong)" : "var(--muted)"} strokeWidth="2" />
            <text x={cx + 12} y={cy + 4} fontSize="12" fill="var(--ink)" fontWeight="500">{c.name.replace("demo:", "")}</text>
            <title>{`${c.name}: ${pct(c.quality)} pass, ${money(c.cost_per_success!)} per success, P95 ${c.p95_seconds?.toFixed(2)} s`}</title>
          </g>
        );
      })}
    </svg>
  );
}
