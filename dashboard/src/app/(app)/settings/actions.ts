"use server";

import { attempt, put } from "@/lib/actions";

export async function saveSettings(formData: FormData) {
  const changes = {
    retention_days: Number(formData.get("retention_days")),
    sample_rate: Number(formData.get("sample_rate")) / 100,
    redact: formData.get("redact") === "on",
    store_content: formData.get("store_content") === "on",
  };
  await attempt("/settings", async () => put("/v1/settings", changes), "Saved.");
}
