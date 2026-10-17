import type { CSSProperties } from "react";
import { pct } from "@/lib/format";
import type { Interval } from "@/lib/types";

interface Props {
  rate: number | null;
  ci?: Interval;
  n?: number;
  kind?: "measured" | "estimated";
  /** A limit from a policy, drawn as a dashed line. */
  threshold?: number;
  /** Colour the interval red. Use it when this reading is the problem. */
  bad?: boolean;
  delay?: number;
  compact?: boolean;
}

/** A rate drawn with its uncertainty. Solid = measured. Hatched = estimated (a judge's opinion). */
export function Rail({ rate, ci, n, kind = "measured", threshold, bad, delay = 0, compact }: Props) {
  const style = { "--delay": `${delay}ms` } as CSSProperties;
  const range = ci ? `${pct(ci[0], 0)}–${pct(ci[1], 0)}` : null;
  const label = rate == null ? "No data" : `${pct(rate)}${range ? `, interval ${range}` : ""}${n != null ? `, ${n} cases` : ""}, ${kind}`;
  return (
    <div className="reading">
      <span className="reading__num num">{pct(rate)}</span>
      <div className={`irail irail--${kind}${bad ? " irail--bad" : ""}`} style={style} role="img" aria-label={label}>
        <span className="irail__track" />
        {[0, 25, 50, 75, 100].map((t) => <span key={t} className="irail__tick" style={{ left: `${t}%` }} />)}
        {ci && rate != null && (
          <span className="irail__ci" style={{ left: `${ci[0] * 100}%`, width: `max(${(ci[1] - ci[0]) * 100}%, 3px)` }} />
        )}
        {threshold != null && <span className="irail__thr" style={{ left: `${threshold * 100}%` }} title={`Limit ${pct(threshold, 0)}`} />}
        {rate != null && <span className="irail__pt" style={{ left: `${rate * 100}%` }} />}
      </div>
      {!compact && (
        <span className="reading__meta num">
          {range ?? ""}{n != null ? `${range ? " · " : ""}n=${n}` : ""}
        </span>
      )}
    </div>
  );
}
