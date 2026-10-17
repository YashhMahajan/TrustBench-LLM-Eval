export type Interval = [number, number] | null;
export type GateStatus = "pass" | "fail" | "inconclusive" | "incomplete";

export interface Me { workspace: { id: string; name: string }; role: "read" | "write" | "admin"; version: string }

export interface ModelRate { rate: number | null; ci: Interval; n: number }
export interface RunRow {
  id: string; name: string | null; status: string; created_at: string; finished_at: string | null;
  dataset: string; dataset_version: string; cases: number; models: Record<string, ModelRate>;
  cost_usd: number; judge_cost_usd: number; baseline: string | null;
}

export interface Score { n: number; rate: number | null; ci: Interval; method: string }
export interface Operations {
  calls: number; call_failure_rate: number | null; retry_rate: number | null; timeout_rate: number | null;
  latency_ms: Record<string, number | null>; latency_n: number; cost_usd: number | null; cost_per_success_usd: number | null; priced_share: number | null;
}
export interface WeakSlice { dimension: string; value: string; n: number; rate: number; rest_rate: number; p_value: number }
export interface ModelSummary {
  overall: Score; by_task: Record<string, Score>; left_out: { skipped_cases: number; evaluator_errors: number };
  operations: Operations; weak_slices: WeakSlice[]; needs_human_review: number;
  hallucination?: Record<string, number | null>; calibration?: Calibration;
}
export interface RunDetail {
  run_id: string; status: string; dataset: string; dataset_version: string; judge_cost_usd: number; name: string | null;
  created_at: string; finished_at: string | null; software_version: string; cost_usd: number; config: { models: string[]; options: Record<string, unknown> };
  models: Record<string, ModelSummary>;
}

export interface CaseRow {
  case_id: string; model: string; repetition: number; task: string; result: "pass" | "fail" | "error" | "skipped";
  labels: string[]; latency_ms: number | null; cost_usd: number | null; attempts: number; output: string; question: string;
}
export interface Check { rule: string; status: GateStatus; required: string; observed: string; evidence: string }
export interface Decision {
  id: number; run_id: string; model: string; baseline_run_id: string | null; policy: string; status: GateStatus; allowed: boolean;
  checks: Check[]; warnings: string[]; at: string;
}

export interface Delta {
  label: string; n: number; baseline: number | null; candidate: number | null; diff: number | null; ci: Interval; p_value: number | null;
  method: string; verdict: string; candidate_only_pass: number | null; baseline_only_pass: number | null;
}
export interface Compare {
  candidate: { run: string; model: string }; baseline: { run: string; model: string }; min_effect: number;
  comparison: { overall: Delta; by_task: Record<string, Delta>; paired_cases: number; unpaired_cases: number; warnings: string[] };
  worse_slices: { dimension: string; value: string; n: number; baseline: number; candidate: number; diff: number; p_value: number | null }[];
  operations: { baseline: Operations; candidate: Operations };
}

export interface Calibration {
  n: number; unreadable: number; accuracy: number | null; mean_confidence: number | null; brier: number | null; brier_ci: Interval;
  ece: number | null; auroc: number | null; risk_at_50: number | null; risk_at_80: number | null;
  bins: { low: number; high: number; n: number; mean_confidence: number; accuracy: number }[]; warnings: string[];
}
export interface RateObj { value: number | null; k: number; n: number; ci: Interval }
export interface Monitor {
  window_days: number; traces: number; feature: string | null; unmeasured: string[];
  measured: Record<string, unknown> & { error_rate?: RateObj; latency_ms?: Record<string, number>; cost_usd?: { total: number | null; per_trace: number | null; priced_share: number };
    user_feedback?: { bad: RateObj; rated_share: number; note: string }; empty_answer_rate?: RateObj };
  estimated: Record<string, { pass_rate: RateObj; coverage: number }>;
  online_checks?: { sampled_share: number; evaluated: number; pending: number; failed_to_evaluate: number };
  daily: { date: string; traces: number; error_rate: number; latency_p95_ms: number }[];
}
export interface DriftFinding { feature: string; kind: string; statistic: number | null; p_value: number | null; drifted: boolean; level: string; reference: string; current: string }
export interface Drift { reference_traces: number; current_traces: number; warnings: string[]; note: string; findings: DriftFinding[] }
export interface Proposal { id: number; dataset: string; case_id: string; method: string; status: string; answer_checked: boolean; source_case: string | null; note: string; spec: { input: Record<string, string>; expected: Record<string, unknown>; evaluation: { evaluators: { type: string }[] } } }

export interface RateK { k: number; n: number; ci: Interval }
export interface Group { name: string; pairs: number; original_rate: number; variant_rate: number; break_rate: RateK }
export interface Robustness {
  pairs: number; original_rate: number | null; variant_rate: number | null; degradation: number | null; degradation_ci: Interval;
  break_rate: RateK | null; fix_rate: RateK | null; consistency: RateK | null; consistently_wrong: RateK | null;
  by_transform: Group[]; by_task: Group[]; worst_transform: Group | null; worst_task: Group | null; fragile_cases: Record<string, string[]>; warnings: string[];
}
export interface FailuresReport {
  cases_with_a_result: number; failed_cases: number;
  labels: { label: string; meaning: string; cases: number; share: number; case_ids: string[] }[];
  clusters: { id: number; size: number; share: number; terms: string[]; labels: Record<string, number>; tasks: Record<string, number>; cohesion: number; follow_up: string; examples: string[] }[];
  unclustered: string[]; rag_stages: Record<string, number>; needs_review: string[];
  priority: { name: string; cases: number; severity: number; weight: number; frequency_pct: number; exposure: number; detectability: number; score: number }[];
}
export interface SliceRow { dimension: string; value: string; n: number; rate: number | null; ci: Interval; rest_rate: number | null; p_value: number | null; weak: boolean; underpowered: boolean }
export interface CaseDetail {
  case_id: string; task: string; metadata: Record<string, unknown>;
  executions: {
    model: string; repetition: number; status: string; input: Record<string, string>; expected: Record<string, unknown>; output: string | null; error: string | null;
    attempts: { attempt: number; ok: boolean; error: string | null }[]; labels: string[];
    results: { evaluator: string; rubric_version: string; passed: boolean; score: number; rationale: string; details: Record<string, unknown> }[];
    rag: { stage: string | null; evidence_in_context: boolean | null; evidence_position: number | null; explanation: string };
  }[];
}
export interface Baseline { name: string; run_id: string; model: string | null; set_at: string }
