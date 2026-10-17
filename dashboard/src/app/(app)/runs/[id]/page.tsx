import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { ago, shortId, usd } from "@/lib/format";
import type { RunDetail } from "@/lib/types";
import { PageHead } from "@/components/Panel";
import { Stamp } from "@/components/Stamp";
import { Segmented, Tabs } from "@/components/Controls";
import { Summary } from "./summary";
import { Cases } from "./cases";
import { Failures } from "./failures";
import { Robustness } from "./robustness";
import { CalibrationTab } from "./calibration";

export const metadata = { title: "Run" };

export type SP = { tab?: string; model?: string; case?: string; result?: string; task?: string; q?: string };

export default async function RunPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<SP> }) {
  const { id } = await params;
  const sp = await searchParams;
  const run = await apiOrNull<RunDetail>(`/v1/runs/${id}`);
  if (!run) notFound();
  const models = Object.keys(run.models);
  const model = sp.model && models.includes(sp.model) ? sp.model : models[0];
  const tab = sp.tab ?? "summary";
  const q = (extra: Record<string, string | undefined>) => {
    const u = new URLSearchParams();
    const merged = { tab: sp.tab, model: models.length > 1 ? model : undefined, ...extra };
    Object.entries(merged).forEach(([k, v]) => v && u.set(k, v));
    return `/runs/${id}?${u.toString()}`;
  };
  const summary = run.models[model];
  const hasCal = !!summary.calibration && summary.calibration.n > 0;

  return (
    <>
      <PageHead
        title={run.name ?? `Run ${shortId(id)}`}
        sub={<><span className="mono">{run.dataset}</span> <span className="mono">{run.dataset_version.slice(0, 8)}</span> · {ago(run.created_at)} · cost {usd(run.cost_usd, 3)}{run.judge_cost_usd > 0 && ` (judge ${usd(run.judge_cost_usd, 3)})`}</>}
        aside={<div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {run.status !== "complete" && <Stamp status="incomplete" />}
          {models.length > 1 && <Segmented current={model} items={models.map((m) => ({ id: m, label: m.replace("demo:", ""), href: q({ model: m }) }))} />}
          <Link href={`/regression?candidate=${id}`} className="btn btn--sm">Compare</Link>
          <a href={`/runs/${id}/report${models.length > 1 ? `?model=${encodeURIComponent(model)}` : ""}`} className="btn btn--sm" download>Download report</a>
        </div>}
      />
      <Tabs current={tab} items={[
        { id: "summary", label: "Summary", href: q({ tab: "summary", case: undefined }) },
        { id: "cases", label: "Cases", href: q({ tab: "cases", case: undefined }) },
        { id: "failures", label: "Failures", href: q({ tab: "failures", case: undefined }) },
        { id: "robustness", label: "Robustness", href: q({ tab: "robustness", case: undefined }) },
        ...(hasCal ? [{ id: "calibration", label: "Calibration", href: q({ tab: "calibration", case: undefined }) }] : []),
      ]} />
      {tab === "cases" ? <Cases id={id} model={model} models={models} sp={sp} q={q} />
        : tab === "failures" ? <Failures id={id} model={model} />
        : tab === "robustness" ? <Robustness id={id} model={model} />
        : tab === "calibration" && hasCal ? <CalibrationTab id={id} model={model} />
        : <Summary run={run} model={model} id={id} />}
    </>
  );
}
