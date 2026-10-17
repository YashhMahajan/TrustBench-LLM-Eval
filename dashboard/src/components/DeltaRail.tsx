import type { CSSProperties } from "react";
import { pp } from "@/lib/format";
import type { Interval } from "@/lib/types";

interface Props { diff: number | null; ci: Interval; minEffect: number; tone: "pass" | "fail" | "warn" | "neutral"; delay?: number }

/** A change drawn as a bar around zero. The hatched band is the change that does not matter in practice. */
export function DeltaRail({ diff, ci, minEffect, tone, delay = 0 }: Props) {
  if (diff == null || !ci) return <span className="hint">–</span>;
  const reach = Math.max(0.1, Math.ceil(Math.max(Math.abs(ci[0]), Math.abs(ci[1]), minEffect * 3) / 0.05) * 0.05);
  const at = (v: number) => `${((v + reach) / (2 * reach)) * 100}%`;
  return (
    <div className="drail" style={{ "--delay": `${delay}ms` } as CSSProperties} role="img"
      aria-label={`Change ${pp(diff)}, 95% interval ${pp(ci[0])} to ${pp(ci[1])}`}>
      <span className="drail__band" style={{ left: at(-minEffect), width: `${(minEffect / reach) * 100}%` }} />
      <span className="drail__zero" />
      <span className={`drail__ci drail__ci--${tone}`} style={{ left: at(ci[0]), width: `max(${((ci[1] - ci[0]) / (2 * reach)) * 100}%, 3px)` }} />
      <span className="drail__pt" style={{ left: at(diff) }} />
      <span className="drail__end drail__end--l num">{pp(-reach, 0)}</span>
      <span className="drail__end drail__end--r num">{pp(reach, 0)}</span>
    </div>
  );
}
