"use server";

import { attempt, post } from "@/lib/actions";

export async function promote(formData: FormData) {
  const id = String(formData.get("trace"));
  const answer = String(formData.get("answer") ?? "").trim();
  await attempt("/monitor#flagged", async () => post(`/v1/traces/${encodeURIComponent(id)}/promote`, answer ? { answer } : {}), "Proposed as a test. Review it under Benchmarks.");
}
