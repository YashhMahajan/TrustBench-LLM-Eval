"use server";

import { revalidatePath } from "next/cache";
import { attempt, post } from "@/lib/actions";

export async function review(formData: FormData) {
  const id = String(formData.get("id"));
  const decision = formData.get("decision") === "reject" ? "reject" : "approve";
  await attempt("/benchmarks#proposals", async () => {
    await post(`/v1/proposals/${encodeURIComponent(id)}/${decision}`, { checked_answers: formData.get("checked") === "on" });
    revalidatePath("/benchmarks");
  }, decision === "approve" ? "Approved. Export approved cases with: trustbench proposals export DATASET" : "Rejected.");
}
