export const pct = (x: number | null | undefined, digits = 1) => (x == null ? "–" : `${(x * 100).toFixed(digits)}%`);
export const pp = (x: number | null | undefined, digits = 1) => (x == null ? "–" : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(digits)} pts`);
export const ms = (v: number | null | undefined) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);
export const usd = (v: number | null | undefined, digits = 4) => (v == null ? "unknown" : v < 0.01 && v > 0 ? `$${v.toFixed(5)}` : `$${v.toFixed(digits)}`);
export const shortId = (s: string) => (s.length > 12 ? s.slice(0, 12) : s);

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "–";
  const sec = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (sec < 90) return "just now";
  if (sec < 5400) return `${Math.round(sec / 60)} min ago`;
  if (sec < 129600) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} days ago`;
}

export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC" : "–";

export const label = (s: string) => s.replace(/_/g, " ");

export const VERDICT_TEXT: Record<string, { text: string; tone: "pass" | "fail" | "warn" | "neutral" }> = {
  meaningful_improvement: { text: "Better", tone: "pass" },
  improvement_below_threshold: { text: "Slightly better", tone: "pass" },
  equivalent: { text: "No real change", tone: "neutral" },
  inconclusive: { text: "Cannot tell", tone: "warn" },
  regression_below_threshold: { text: "Slightly worse", tone: "fail" },
  meaningful_regression: { text: "Worse", tone: "fail" },
};

export const STATUS_HEADLINE: Record<string, string> = {
  pass: "Ready to release",
  fail: "Release blocked",
  inconclusive: "Not enough evidence to decide",
  incomplete: "The evaluation did not finish",
};
