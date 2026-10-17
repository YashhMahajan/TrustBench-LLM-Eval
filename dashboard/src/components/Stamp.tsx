import type { GateStatus } from "@/lib/types";

const TEXT: Record<GateStatus, string> = { pass: "Pass", fail: "Fail", inconclusive: "Inconclusive", incomplete: "Incomplete" };

export function Stamp({ status, large }: { status: GateStatus; large?: boolean }) {
  return <span className={`stamp stamp--${status}${large ? " stamp--lg" : ""}`}>{TEXT[status]}</span>;
}

export function Kind({ kind }: { kind: "measured" | "estimated" | "unmeasured" }) {
  const text = { measured: "Measured", estimated: "Estimated", unmeasured: "Not measured" }[kind];
  return <span className={`kind kind--${kind}`}><i />{text}</span>;
}

export function KindLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1" aria-label="How to read the bars">
      <Kind kind="measured" /><Kind kind="estimated" /><Kind kind="unmeasured" />
    </div>
  );
}
